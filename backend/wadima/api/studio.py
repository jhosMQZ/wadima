"""Project Studio: proyectos de código, editor de archivos, Git, ejecuciones aisladas y Live Preview."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima import audit
from wadima import org as orgsvc
from wadima.activity import log_activity
from wadima.api.areas import slugify
from wadima.api.serial import iso
from wadima.config import get_settings
from wadima.db import get_db
from wadima.models import Project, StudioProject, StudioRun, User
from wadima.realtime import emit
from wadima.security.auth import Principal, client_ip, now_utc, require_password_ok
from wadima.security.perms import accessible_area_ids, require_area
from wadima.services.merge import three_way_merge
from wadima.studio import gitops, runner, workspace
from wadima.studio.preview import notify_reload

router = APIRouter(prefix="/api/studio", tags=["studio"])


def sp_out(sp: StudioProject) -> dict:
    s = get_settings()
    dev = runner.dev_server(sp.slug)
    return {
        "id": str(sp.id),
        "area_id": str(sp.area_id),
        "project_id": str(sp.project_id) if sp.project_id else None,
        "name": sp.name,
        "slug": sp.slug,
        "template": sp.template,
        "template_name": workspace.TEMPLATES[sp.template]["name"],
        "profiles": runner.PROFILES.get(sp.template, []),
        "created_at": iso(sp.created_at),
        "updated_at": iso(sp.updated_at),
        "archived_at": iso(sp.archived_at),
        "preview_url": f"{s.preview_public_origin}/p/{sp.slug}/",
        "preview": {
            "available": sp.template == "static" or dev is not None,
            "dev_running": dev is not None,
            "dev_ready": bool(dev and dev.ready),
            "dev_run_id": str(dev.run_id) if dev else None,
        },
    }


def _author(user: User) -> tuple[str, str]:
    return user.display_name, user.email or f"{user.username}@wadima.local"


def _sp(db: Session, principal: Principal, sp_id: uuid.UUID, min_role: str = "viewer") -> StudioProject:
    sp = db.get(StudioProject, sp_id)
    if sp is None or sp.archived_at is not None:
        raise HTTPException(404, "Proyecto de Studio no encontrado")
    require_area(db, principal, sp.area_id, min_role)
    return sp


def _root(sp: StudioProject):
    root = workspace.project_root(sp.slug)
    if not root.exists():
        raise HTTPException(410, "El espacio de trabajo no existe en disco")
    return root


def _ws_error(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


@router.get("/templates")
def templates(principal: Principal = Depends(require_password_ok)):
    tools = runner.toolchains()
    return [
        {
            "key": k,
            "name": t["name"],
            "needs": t["needs"],
            "available": all(tools.get(n) for n in t["needs"]),
            "profiles": runner.PROFILES.get(k, []),
        }
        for k, t in workspace.TEMPLATES.items()
        if t.get("create", True)
    ]


@router.get("/toolchains")
def toolchains(principal: Principal = Depends(require_password_ok)):
    return runner.toolchains()


class CreateIn(BaseModel):
    area_id: uuid.UUID
    project_id: uuid.UUID | None = None
    name: str = Field(min_length=2, max_length=120)
    template: str = Field(pattern="^(vite-vanilla|static|python|empty)$")


@router.get("/projects")
def list_projects(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    ids = accessible_area_ids(db, principal)
    if not ids:
        return []
    rows = db.execute(
        select(StudioProject)
        .where(StudioProject.area_id.in_(ids), StudioProject.archived_at.is_(None))
        .order_by(StudioProject.updated_at.desc())
    ).scalars()
    return [sp_out(sp) for sp in rows]


@router.post("/projects")
def create_project(
    body: CreateIn, request: Request, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    require_area(db, principal, body.area_id, "editor")
    if body.project_id:
        p = db.get(Project, body.project_id)
        if p is None or p.area_id != body.area_id:
            raise HTTPException(422, "El proyecto no pertenece al área")
    tools = runner.toolchains()
    missing = [n for n in workspace.TEMPLATES[body.template]["needs"] if not tools.get(n)]
    if missing:
        raise HTTPException(422, f"Faltan herramientas en el servidor: {', '.join(missing)}")
    base = slugify(body.name)[:60] or "proyecto"
    slug, n = base, 2
    while (
        db.execute(select(StudioProject.id).where(StudioProject.slug == slug)).first()
        or workspace.project_root(slug).exists()
    ):
        slug = f"{base}-{n}"
        n += 1
    try:
        brand = orgsvc.get_org(db).platform_name
        root = workspace.create_from_template(slug, body.template, brand)
        gitops.init(root, _author(principal.user), brand)
    except (workspace.WorkspaceError, gitops.GitError) as exc:
        raise HTTPException(500, f"No se pudo crear el espacio de trabajo: {exc}") from exc
    sp = StudioProject(
        area_id=body.area_id,
        project_id=body.project_id,
        name=body.name.strip(),
        slug=slug,
        template=body.template,
        created_by=principal.id,
    )
    db.add(sp)
    db.flush()
    audit.record(
        db,
        "studio.project_created",
        actor_id=principal.id,
        target_type="studio_project",
        target_id=sp.id,
        area_id=sp.area_id,
        ip=client_ip(request),
        data={"template": body.template, "slug": slug},
    )
    log_activity(
        db,
        "studio.created",
        f"Creó el proyecto de código «{sp.name}» ({body.template})",
        user=principal.user,
        area_id=sp.area_id,
        project_id=sp.project_id,
        ref_type="studio",
        ref_id=sp.id,
    )
    db.commit()
    return sp_out(sp)


@router.post("/projects/{sp_id}/upload")
def upload_files(
    sp_id: uuid.UUID,
    request: Request,
    files: list[UploadFile] | None = File(None),
    archive: UploadFile | None = File(None),
    target: str = Form(""),
    overwrite: bool = Form(False),
    strip_root: bool = Form(True),
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    """Sube archivos propios (una carpeta del equipo, varios archivos o un ZIP) al proyecto. Las rutas relativas
    llegan en el nombre de cada archivo. No sobrescribe nada salvo que se pida; los cambios quedan pendientes de
    commit en el panel Git (el historial conserva la versión anterior de lo que se sustituya)."""
    sp = _sp(db, principal, sp_id, "editor")
    root = _root(sp)
    if not files and archive is None:
        raise HTTPException(422, "No se recibió ningún archivo")
    try:
        if archive is not None:
            data = archive.file.read(workspace.IMPORT_MAX_TOTAL_BYTES + 1)
            if len(data) > workspace.IMPORT_MAX_TOTAL_BYTES:
                raise HTTPException(413, "El ZIP supera 300 MB")
            entries = list(workspace.zip_items(data))
        else:
            entries = [
                ((f.filename or "archivo"), f.file.read(workspace.IMPORT_MAX_FILE_BYTES + 1)) for f in files or []
            ]
        strip = workspace.common_root([e[0] for e in entries]) if strip_root else ""
        report = workspace.import_items(root, entries, target=target, overwrite=overwrite, strip=strip)
    except workspace.WorkspaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    detected = None
    if sp.template == "empty" and report.written:
        detected = workspace.detect_kind(root)
        if detected != sp.template:
            sp.template = detected
    if report.written:
        audit.record(
            db,
            "studio.files_uploaded",
            actor_id=principal.id,
            target_type="studio_project",
            target_id=sp.id,
            area_id=sp.area_id,
            ip=client_ip(request),
            data={
                "files": len(report.written),
                "bytes": report.bytes,
                "overwrite": overwrite,
                "zip": archive is not None,
            },
        )
        log_activity(
            db,
            "studio.upload",
            f"Subió {len(report.written)} archivo(s) a «{sp.name}»",
            user=principal.user,
            area_id=sp.area_id,
            project_id=sp.project_id,
            ref_type="studio",
            ref_id=sp.id,
        )
        sp.updated_at = now_utc()
        emit(db, "studio.file_changed", area_id=sp.area_id, project_id=str(sp.id), path="", by=str(principal.id))
    db.commit()
    return {
        **report.out(),
        "stripped_root": strip or None,
        "template": sp.template,
        "detected": detected,
        "project": sp_out(sp),
    }


@router.get("/projects/{sp_id}")
def get_project(sp_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    sp = _sp(db, principal, sp_id)
    root = _root(sp)
    try:
        info = {"branch": gitops.branch(root), "changes": len(gitops.status(root))}
    except gitops.GitError as exc:
        info = {"branch": None, "changes": None, "error": str(exc)}
    return sp_out(sp) | {"git": info}


class ConfirmIn(BaseModel):
    confirm_slug: str


@router.post("/projects/{sp_id}/archive")
def archive_project(
    sp_id: uuid.UUID,
    body: ConfirmIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    sp = _sp(db, principal, sp_id, "owner")
    if body.confirm_slug != sp.slug:
        raise HTTPException(422, "La confirmación no coincide")
    runner.stop_dev(sp.slug)
    sp.archived_at = now_utc()
    audit.record(
        db,
        "studio.project_archived",
        actor_id=principal.id,
        target_type="studio_project",
        target_id=sp.id,
        area_id=sp.area_id,
        ip=client_ip(request),
    )
    db.commit()
    return {"ok": True, "note": "Los archivos se conservan en disco."}


# --------------------------------------------------------------------------- archivos
@router.get("/projects/{sp_id}/tree")
def tree(sp_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    sp = _sp(db, principal, sp_id)
    return workspace.tree(_root(sp))


@router.get("/projects/{sp_id}/file")
def read_file(
    sp_id: uuid.UUID, path: str, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    sp = _sp(db, principal, sp_id)
    try:
        content, h = workspace.read_text(_root(sp), path)
    except workspace.WorkspaceError as exc:
        raise _ws_error(exc) from exc
    return {"path": path, "content": content, "hash": h}


class SaveIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=1_000_000)
    base_hash: str | None = None  # None = archivo nuevo
    base_content: str | None = Field(default=None, max_length=1_000_000)
    force: bool = False


@router.put("/projects/{sp_id}/file")
def save_file(
    sp_id: uuid.UUID, body: SaveIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    """Guardado sin sobrescrituras silenciosas: si el archivo cambió desde que se abrió, se intenta una fusión a
    tres bandas y, si hay solapamientos, se devuelve 409 con la versión actual."""
    sp = _sp(db, principal, sp_id, "editor")
    root = _root(sp)
    try:
        current = workspace.current_hash(root, body.path)
        content = body.content
        merged = False
        if not body.force and current != body.base_hash:
            if current is None:
                raise HTTPException(409, {"message": "El archivo fue eliminado por otra persona", "current": None})
            cur_text, _ = workspace.read_text(root, body.path)
            if body.base_content is None:
                raise HTTPException(
                    409, {"message": "El archivo cambió en el servidor", "current": cur_text, "hash": current}
                )
            result = three_way_merge(body.base_content, body.content, cur_text)
            if result.conflicts:
                raise HTTPException(
                    409,
                    {
                        "message": f"Cambios simultáneos solapados ({result.conflicts})",
                        "current": cur_text,
                        "hash": current,
                        "merged_with_markers": result.merged,
                    },
                )
            content, merged = result.merged, True
        new_hash = workspace.write_text(root, body.path, content)
    except workspace.WorkspaceError as exc:
        raise _ws_error(exc) from exc
    emit(
        db,
        "studio.file_changed",
        area_id=sp.area_id,
        project_id=str(sp.id),
        path=body.path,
        hash=new_hash,
        by=str(principal.id),
    )
    sp.updated_at = now_utc()
    db.commit()
    if sp.template == "static":
        notify_reload(sp.slug)
    return {"path": body.path, "hash": new_hash, "merged": merged, "content": content if merged else None}


class CreateFileIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    kind: str = Field(default="file", pattern="^(file|dir)$")


@router.post("/projects/{sp_id}/files")
def create_file(
    sp_id: uuid.UUID,
    body: CreateFileIn,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    sp = _sp(db, principal, sp_id, "editor")
    root = _root(sp)
    try:
        target = workspace.safe_path(root, body.path)
        if target.exists():
            raise HTTPException(409, "Ya existe")
        if body.kind == "dir":
            target.mkdir(parents=True)
        else:
            workspace.write_text(root, body.path, "")
    except workspace.WorkspaceError as exc:
        raise _ws_error(exc) from exc
    emit(db, "studio.file_changed", area_id=sp.area_id, project_id=str(sp.id), path=body.path, by=str(principal.id))
    db.commit()
    return {"ok": True}


class RenameIn(BaseModel):
    src: str = Field(min_length=1, max_length=300)
    dst: str = Field(min_length=1, max_length=300)


@router.post("/projects/{sp_id}/files/rename")
def rename_file(
    sp_id: uuid.UUID, body: RenameIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    sp = _sp(db, principal, sp_id, "editor")
    try:
        workspace.rename_path(_root(sp), body.src, body.dst)
    except workspace.WorkspaceError as exc:
        raise _ws_error(exc) from exc
    emit(db, "studio.file_changed", area_id=sp.area_id, project_id=str(sp.id), path=body.dst, by=str(principal.id))
    db.commit()
    return {"ok": True}


@router.delete("/projects/{sp_id}/file")
def delete_file(
    sp_id: uuid.UUID, path: str, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    sp = _sp(db, principal, sp_id, "editor")
    try:
        workspace.delete_path(_root(sp), path)
    except workspace.WorkspaceError as exc:
        raise _ws_error(exc) from exc
    emit(db, "studio.file_changed", area_id=sp.area_id, project_id=str(sp.id), path=path, by=str(principal.id))
    db.commit()
    return {"ok": True, "note": "Recuperable desde Git si el archivo estaba confirmado."}


# --------------------------------------------------------------------------- Git
@router.get("/projects/{sp_id}/git/status")
def git_status(sp_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    sp = _sp(db, principal, sp_id)
    root = _root(sp)
    try:
        return {"branch": gitops.branch(root), "changes": gitops.status(root), "branches": gitops.branches(root)}
    except gitops.GitError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/projects/{sp_id}/git/log")
def git_log(
    sp_id: uuid.UUID,
    path: str | None = None,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    sp = _sp(db, principal, sp_id)
    try:
        if path:
            workspace.safe_path(_root(sp), path)
        return gitops.log(_root(sp), path)
    except (gitops.GitError, workspace.WorkspaceError) as exc:
        raise HTTPException(400, str(exc)) from exc


class CommitIn(BaseModel):
    message: str = Field(min_length=3, max_length=500)


@router.post("/projects/{sp_id}/git/commit")
def git_commit(
    sp_id: uuid.UUID, body: CommitIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    sp = _sp(db, principal, sp_id, "editor")
    try:
        sha = gitops.commit(_root(sp), body.message, _author(principal.user))
    except gitops.GitError as exc:
        raise HTTPException(400, str(exc)) from exc
    if sha is None:
        raise HTTPException(409, "No hay cambios que confirmar")
    log_activity(
        db,
        "studio.commit",
        f"Commit en «{sp.name}»: {body.message[:150]}",
        user=principal.user,
        area_id=sp.area_id,
        project_id=sp.project_id,
        ref_type="studio",
        ref_id=sp.id,
        data={"commit": sha},
    )
    emit(db, "studio.git", area_id=sp.area_id, project_id=str(sp.id), commit=sha)
    db.commit()
    return {"commit": sha}


@router.get("/projects/{sp_id}/git/diff")
def git_diff(
    sp_id: uuid.UUID,
    path: str | None = None,
    ref_from: str | None = None,
    ref_to: str | None = None,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    sp = _sp(db, principal, sp_id)
    try:
        if path:
            workspace.safe_path(_root(sp), path)
        return {"diff": gitops.diff(_root(sp), path, ref_from, ref_to)}
    except (gitops.GitError, workspace.WorkspaceError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/projects/{sp_id}/git/show")
def git_show(
    sp_id: uuid.UUID,
    ref: str,
    path: str,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    sp = _sp(db, principal, sp_id)
    try:
        workspace.safe_path(_root(sp), path)
        return {"content": gitops.show(_root(sp), ref, path)}
    except (gitops.GitError, workspace.WorkspaceError) as exc:
        raise HTTPException(400, str(exc)) from exc


class RestoreIn(BaseModel):
    ref: str = Field(min_length=4, max_length=100)
    path: str = Field(min_length=1, max_length=300)


@router.post("/projects/{sp_id}/git/restore")
def git_restore(
    sp_id: uuid.UUID,
    body: RestoreIn,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    sp = _sp(db, principal, sp_id, "editor")
    try:
        workspace.safe_path(_root(sp), body.path)
        gitops.restore(_root(sp), body.ref, body.path)
    except (gitops.GitError, workspace.WorkspaceError) as exc:
        raise HTTPException(400, str(exc)) from exc
    emit(db, "studio.file_changed", area_id=sp.area_id, project_id=str(sp.id), path=body.path, by=str(principal.id))
    db.commit()
    return {"ok": True}


class BranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.post("/projects/{sp_id}/git/branches")
def git_branch(
    sp_id: uuid.UUID, body: BranchIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    sp = _sp(db, principal, sp_id, "editor")
    try:
        gitops.create_branch(_root(sp), body.name)
    except gitops.GitError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.post("/projects/{sp_id}/git/checkout")
def git_checkout(
    sp_id: uuid.UUID, body: BranchIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    sp = _sp(db, principal, sp_id, "editor")
    try:
        gitops.checkout(_root(sp), body.name)
    except gitops.GitError as exc:
        raise HTTPException(409, str(exc)) from exc
    emit(db, "studio.git", area_id=sp.area_id, project_id=str(sp.id), branch=body.name)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- ejecuciones
class RunIn(BaseModel):
    profile: str = Field(pattern="^(install|dev|build|test|run|console)$")
    command: str | None = Field(default=None, max_length=500)


def run_out(r: StudioRun) -> dict:
    snap = runner.snapshot(r.id)
    return {
        "id": str(r.id),
        "profile": r.profile,
        "command": r.command,
        "status": snap["status"] if snap else r.status,
        "exit_code": snap["exit_code"] if snap else r.exit_code,
        "port": r.port,
        "ready": snap["ready"] if snap else False,
        "started_at": iso(r.started_at),
        "finished_at": iso(r.finished_at),
        "user_id": str(r.user_id) if r.user_id else None,
    }


@router.post("/projects/{sp_id}/runs")
def start_run(
    sp_id: uuid.UUID,
    body: RunIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    sp = _sp(db, principal, sp_id, "editor")
    _root(sp)
    try:
        row = runner.start(sp, body.profile, principal.user, body.command)
    except runner.RunError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.profile == "console":
        audit.record(
            db,
            "studio.console",
            actor_id=principal.id,
            target_type="studio_project",
            target_id=sp.id,
            area_id=sp.area_id,
            ip=client_ip(request),
            data={"command": (body.command or "")[:300]},
        )
        db.commit()
    return run_out(row)


@router.get("/projects/{sp_id}/runs")
def list_runs(sp_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    sp = _sp(db, principal, sp_id)
    rows = db.execute(
        select(StudioRun).where(StudioRun.studio_project_id == sp.id).order_by(StudioRun.started_at.desc()).limit(30)
    ).scalars()
    return [run_out(r) for r in rows]


@router.get("/projects/{sp_id}/runs/{run_id}")
def get_run(
    sp_id: uuid.UUID,
    run_id: uuid.UUID,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    sp = _sp(db, principal, sp_id)
    r = db.get(StudioRun, run_id)
    if r is None or r.studio_project_id != sp.id:
        raise HTTPException(404, "Ejecución no encontrada")
    snap = runner.snapshot(run_id)
    return run_out(r) | {"log": snap["lines"] if snap else r.log_tail.splitlines()}


@router.post("/projects/{sp_id}/runs/{run_id}/stop")
def stop_run(
    sp_id: uuid.UUID,
    run_id: uuid.UUID,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    sp = _sp(db, principal, sp_id, "editor")
    r = db.get(StudioRun, run_id)
    if r is None or r.studio_project_id != sp.id:
        raise HTTPException(404, "Ejecución no encontrada")
    return {"stopped": runner.stop(run_id)}
