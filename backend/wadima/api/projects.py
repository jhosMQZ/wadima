"""Proyectos, tareas, notas Markdown versionadas, comentarios, decisiones, actividad y resúmenes."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wadima import ai_tasks
from wadima.activity import log_activity, notify
from wadima.api.areas import slugify
from wadima.api.serial import iso, user_public
from wadima.db import get_db
from wadima.drive.providers import track_note, track_project_report
from wadima.models import (
    ActivityEvent,
    AIRun,
    Comment,
    Decision,
    Document,
    Note,
    NoteRevision,
    Project,
    ProjectMember,
    Task,
    User,
)
from wadima.realtime import emit
from wadima.security.auth import Principal, now_utc, require_password_ok
from wadima.security.perms import accessible_area_ids, require_area
from wadima.services.jobs import enqueue
from wadima.services.merge import three_way_merge
from wadima.services.summary import area_summary, project_summary

router = APIRouter(prefix="/api", tags=["projects"])

PROJECT_STATUS = "^(planned|active|paused|done|archived)$"
TASK_STATUS = "^(todo|in_progress|blocked|done)$"
NOTE_KINDS = "^(note|spec|requirement|plan|doc|meeting)$"


# --------------------------------------------------------------------------- serialización
def project_out(p: Project, extra: dict | None = None) -> dict:
    return {
        "id": str(p.id),
        "area_id": str(p.area_id),
        "name": p.name,
        "slug": p.slug,
        "description": p.description,
        "status": p.status,
        "created_by": str(p.created_by) if p.created_by else None,
        "created_at": iso(p.created_at),
        "updated_at": iso(p.updated_at),
        "version": p.version,
        "last_summary": p.last_summary,
        "last_summary_at": iso(p.last_summary_at),
        **(extra or {}),
    }


def task_out(t: Task, ai_run: AIRun | None = None) -> dict:
    return {
        "ai_assignee_id": str(t.ai_assignee_id) if t.ai_assignee_id else None,
        "ai_run": {"id": str(ai_run.id), "status": ai_run.status} if ai_run else None,
        "id": str(t.id),
        "project_id": str(t.project_id),
        "parent_id": str(t.parent_id) if t.parent_id else None,
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "assignee_id": str(t.assignee_id) if t.assignee_id else None,
        "due_date": iso(t.due_date),
        "created_by": str(t.created_by) if t.created_by else None,
        "created_at": iso(t.created_at),
        "updated_at": iso(t.updated_at),
        "started_at": iso(t.started_at),
        "completed_at": iso(t.completed_at),
        "version": t.version,
    }


def note_out(n: Note, content: bool = True) -> dict:
    data = {
        "id": str(n.id),
        "area_id": str(n.area_id),
        "project_id": str(n.project_id) if n.project_id else None,
        "title": n.title,
        "kind": n.kind,
        "version": n.version,
        "updated_by": str(n.updated_by) if n.updated_by else None,
        "updated_at": iso(n.updated_at),
        "created_at": iso(n.created_at),
    }
    if content:
        data["content"] = n.content
    return data


def decision_out(d: Decision) -> dict:
    return {
        "id": str(d.id),
        "area_id": str(d.area_id),
        "project_id": str(d.project_id) if d.project_id else None,
        "title": d.title,
        "body": d.body,
        "status": d.status,
        "created_by": str(d.created_by) if d.created_by else None,
        "decided_by": str(d.decided_by) if d.decided_by else None,
        "decided_at": iso(d.decided_at),
        "created_at": iso(d.created_at),
    }


def activity_out(e: ActivityEvent) -> dict:
    return {
        "id": e.id,
        "ts": iso(e.ts),
        "user_id": str(e.user_id) if e.user_id else None,
        "actor_kind": e.actor_kind,
        "actor_name": e.actor_name,
        "area_id": str(e.area_id) if e.area_id else None,
        "project_id": str(e.project_id) if e.project_id else None,
        "kind": e.kind,
        "summary": e.summary,
        "ref_type": e.ref_type,
        "ref_id": e.ref_id,
    }


def _project(db: Session, principal: Principal, project_id: uuid.UUID, min_role: str = "viewer") -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "Proyecto no encontrado")
    require_area(db, principal, p.area_id, min_role)
    return p


def _task(db: Session, principal: Principal, task_id: uuid.UUID, min_role: str = "viewer") -> tuple[Task, Project]:
    t = db.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "Tarea no encontrada")
    return t, _project(db, principal, t.project_id, min_role)


def _note(db: Session, principal: Principal, note_id: uuid.UUID, min_role: str = "viewer") -> Note:
    n = db.get(Note, note_id)
    if n is None or n.deleted_at is not None:
        raise HTTPException(404, "Documento no encontrado")
    require_area(db, principal, n.area_id, min_role)
    return n


def _check_user_in_area(db: Session, user_id: uuid.UUID | None, area_id: uuid.UUID) -> None:
    if user_id is None:
        return
    from wadima.models import AreaMember

    user = db.get(User, user_id)
    is_member = db.execute(
        select(AreaMember.id).where(AreaMember.area_id == area_id, AreaMember.user_id == user_id)
    ).first()
    if user is None or not user.is_active or (not is_member and user.role != "admin"):
        raise HTTPException(422, "El responsable debe ser miembro del área")


# --------------------------------------------------------------------------- proyectos
class ProjectIn(BaseModel):
    area_id: uuid.UUID
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=20000)
    status: str = Field(default="active", pattern=PROJECT_STATUS)


class ProjectPatch(BaseModel):
    version: int
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=20000)
    status: str | None = Field(default=None, pattern=PROJECT_STATUS)


class MemberIn(BaseModel):
    role: str = Field(default="member", pattern="^(lead|member)$")


@router.get("/projects")
def list_projects(
    area_id: uuid.UUID | None = None,
    include_archived: bool = False,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    ids = accessible_area_ids(db, principal)
    if area_id is not None:
        if area_id not in ids:
            raise HTTPException(404, "Área no encontrada")
        ids = [area_id]
    if not ids:
        return []
    q = select(Project).where(Project.area_id.in_(ids))
    if not include_archived:
        q = q.where(Project.status != "archived")
    projects = db.execute(q.order_by(Project.updated_at.desc())).scalars().all()
    pids = [p.id for p in projects]
    counts: dict[uuid.UUID, dict[str, int]] = {}
    if pids:
        for pid, status, n in db.execute(
            select(Task.project_id, Task.status, func.count())
            .where(Task.project_id.in_(pids))
            .group_by(Task.project_id, Task.status)
        ).all():
            counts.setdefault(pid, {})[status] = n
    return [project_out(p, {"task_counts": counts.get(p.id, {})}) for p in projects]


@router.post("/projects")
def create_project(body: ProjectIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    require_area(db, principal, body.area_id, "editor")
    base = slugify(body.name)[:70]
    slug, n = base, 2
    while db.execute(select(Project.id).where(Project.area_id == body.area_id, Project.slug == slug)).first():
        slug = f"{base}-{n}"
        n += 1
    p = Project(
        area_id=body.area_id,
        name=body.name.strip(),
        slug=slug,
        description=body.description,
        status=body.status,
        created_by=principal.id,
    )
    db.add(p)
    db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=principal.id, role="lead"))
    log_activity(
        db,
        "project.created",
        f"Creó el proyecto «{p.name}»",
        user=principal.user,
        area_id=p.area_id,
        project_id=p.id,
        ref_type="project",
        ref_id=p.id,
    )
    db.commit()
    return project_out(p)


@router.get("/projects/{project_id}")
def get_project(
    project_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    p = _project(db, principal, project_id)
    members = db.execute(
        select(ProjectMember, User).join(User, User.id == ProjectMember.user_id).where(ProjectMember.project_id == p.id)
    ).all()
    counts = dict(
        db.execute(select(Task.status, func.count()).where(Task.project_id == p.id).group_by(Task.status)).all()
    )
    documents = (
        db.execute(
            select(Document)
            .where(Document.project_id == p.id, Document.deleted_at.is_(None))
            .order_by(Document.updated_at.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    runs = (
        db.execute(
            select(AIRun)
            .where(AIRun.project_id == p.id, AIRun.shared.is_(True))
            .order_by(AIRun.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    return project_out(
        p,
        {
            "members": [{**user_public(u), "project_role": m.role} for m, u in members],
            "task_counts": counts,
            "documents": [
                {"id": str(d.id), "title": d.title, "status": d.status, "ext": d.ext, "updated_at": iso(d.updated_at)}
                for d in documents
            ],
            "agent_runs": [
                {
                    "id": str(r.id),
                    "model": r.model,
                    "status": r.status,
                    "created_at": iso(r.created_at),
                    "prompt": r.prompt[:200],
                    "user_id": str(r.user_id),
                }
                for r in runs
            ],
        },
    )


@router.patch("/projects/{project_id}")
def update_project(
    project_id: uuid.UUID,
    body: ProjectPatch,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    p = _project(db, principal, project_id, "editor")
    if body.version != p.version:
        raise HTTPException(409, {"message": "El proyecto fue modificado por otra persona", "current": project_out(p)})
    changes = body.model_dump(exclude_none=True, exclude={"version"})
    for k, v in changes.items():
        setattr(p, k, v)
    p.version += 1
    desc = ", ".join(changes) or "sin cambios"
    log_activity(
        db,
        "project.updated",
        f"Actualizó el proyecto «{p.name}» ({desc})",
        user=principal.user,
        area_id=p.area_id,
        project_id=p.id,
        ref_type="project",
        ref_id=p.id,
    )
    emit(db, "project.updated", area_id=p.area_id, id=str(p.id))
    db.commit()
    return project_out(p)


@router.put("/projects/{project_id}/members/{user_id}")
def set_project_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberIn,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    p = _project(db, principal, project_id, "editor")
    _check_user_in_area(db, user_id, p.area_id)
    m = db.execute(
        select(ProjectMember).where(ProjectMember.project_id == p.id, ProjectMember.user_id == user_id)
    ).scalar_one_or_none()
    if m is None:
        db.add(ProjectMember(project_id=p.id, user_id=user_id, role=body.role))
        notify(
            db,
            {user_id} - {principal.id},
            "project.assigned",
            f"Asignado al proyecto «{p.name}»",
            link=f"/projects/{p.id}",
        )
    else:
        m.role = body.role
    emit(db, "project.updated", area_id=p.area_id, id=str(p.id))
    db.commit()
    return {"ok": True}


@router.delete("/projects/{project_id}/members/{user_id}")
def remove_project_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    p = _project(db, principal, project_id, "editor")
    m = db.execute(
        select(ProjectMember).where(ProjectMember.project_id == p.id, ProjectMember.user_id == user_id)
    ).scalar_one_or_none()
    if m:
        db.delete(m)
    emit(db, "project.updated", area_id=p.area_id, id=str(p.id))
    db.commit()
    return {"ok": True}


@router.post("/projects/{project_id}/summary")
def generate_summary(
    project_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    p = _project(db, principal, project_id, "editor")
    summary = project_summary(db, p)
    p.last_summary = summary["markdown"]
    p.last_summary_at = now_utc()
    track_project_report(db, p)
    emit(db, "project.updated", area_id=p.area_id, id=str(p.id))
    db.commit()
    enqueue(
        db,
        "memory.ingest",
        {"source_type": "project_summary", "source_id": str(p.id)},
        dedupe_key=f"memory:project_summary:{p.id}",
    )
    db.commit()
    return summary


# --------------------------------------------------------------------------- tareas
class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)
    assignee_id: uuid.UUID | None = None
    ai_assignee_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    due_date: date | None = None


class TaskPatch(BaseModel):
    version: int
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    status: str | None = Field(default=None, pattern=TASK_STATUS)
    assignee_id: uuid.UUID | None = None
    clear_assignee: bool = False
    ai_assignee_id: uuid.UUID | None = None
    clear_ai_assignee: bool = False
    due_date: date | None = None


@router.get("/projects/{project_id}/tasks")
def list_tasks(
    project_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    p = _project(db, principal, project_id)
    rows = db.execute(select(Task).where(Task.project_id == p.id).order_by(Task.created_at)).scalars().all()
    runs = _latest_ai_runs(db, [t.id for t in rows])
    return [task_out(t, runs.get(t.id)) for t in rows]


def _latest_ai_runs(db: Session, task_ids: list[uuid.UUID]) -> dict[uuid.UUID, AIRun]:
    out: dict[uuid.UUID, AIRun] = {}
    if task_ids:
        for r in db.execute(select(AIRun).where(AIRun.task_id.in_(task_ids)).order_by(AIRun.created_at)).scalars():
            out[r.task_id] = r
    return out


def _assign_ai(db: Session, principal: Principal, t: Task, p: Project, owner_id: uuid.UUID) -> AIRun:
    try:
        owner = ai_tasks.check_assignable(db, owner_id, p.area_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    t.ai_assignee_id = owner.id
    return ai_tasks.queue_for_task(db, t, p, owner, principal.user)


@router.post("/projects/{project_id}/tasks")
def create_task(
    project_id: uuid.UUID,
    body: TaskIn,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    p = _project(db, principal, project_id, "editor")
    _check_user_in_area(db, body.assignee_id, p.area_id)
    if body.parent_id:
        parent = db.get(Task, body.parent_id)
        if parent is None or parent.project_id != p.id:
            raise HTTPException(422, "La tarea padre no pertenece al proyecto")
    t = Task(project_id=p.id, created_by=principal.id, **body.model_dump(exclude={"ai_assignee_id"}))
    db.add(t)
    db.flush()
    run = _assign_ai(db, principal, t, p, body.ai_assignee_id) if body.ai_assignee_id else None
    log_activity(
        db,
        "task.created",
        f"Creó la tarea «{t.title}»",
        user=principal.user,
        area_id=p.area_id,
        project_id=p.id,
        ref_type="task",
        ref_id=t.id,
    )
    if t.assignee_id and t.assignee_id != principal.id:
        notify(
            db, {t.assignee_id}, "task.assigned", f"Nueva tarea: {t.title}", f"Proyecto «{p.name}»", f"/projects/{p.id}"
        )
    emit(db, "task.changed", area_id=p.area_id, project_id=str(p.id), id=str(t.id))
    db.commit()
    return task_out(t, run)


@router.patch("/tasks/{task_id}")
def update_task(
    task_id: uuid.UUID,
    body: TaskPatch,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    t, p = _task(db, principal, task_id, "editor")
    if body.version != t.version:
        raise HTTPException(409, {"message": "La tarea fue modificada por otra persona", "current": task_out(t)})
    changes = body.model_dump(
        exclude_none=True, exclude={"version", "clear_assignee", "ai_assignee_id", "clear_ai_assignee"}
    )
    if body.clear_assignee:
        changes["assignee_id"] = None
    if body.clear_ai_assignee and t.ai_assignee_id:
        ai_tasks.cancel_open_runs(db, t, reason="sin IA asignada")
        t.ai_assignee_id = None
    elif body.ai_assignee_id and body.ai_assignee_id != t.ai_assignee_id:
        _assign_ai(db, principal, t, p, body.ai_assignee_id)
    if "assignee_id" in changes:
        _check_user_in_area(db, changes["assignee_id"], p.area_id)
    old_status = t.status
    for k, v in changes.items():
        setattr(t, k, v)
    t.version += 1
    now = now_utc()
    if body.status and body.status != old_status:
        if body.status == "in_progress" and t.started_at is None:
            t.started_at = now
        if body.status == "done":
            t.completed_at = now
        elif old_status == "done":
            t.completed_at = None
        kind, verb = {
            "in_progress": ("task.started", "Inició"),
            "done": ("task.completed", "Terminó"),
            "blocked": ("task.blocked", "Marcó como bloqueada"),
            "todo": ("task.updated", "Devolvió a pendiente"),
        }[body.status]
        log_activity(
            db,
            kind,
            f"{verb} la tarea «{t.title}»",
            user=principal.user,
            area_id=p.area_id,
            project_id=p.id,
            ref_type="task",
            ref_id=t.id,
        )
    elif changes:
        log_activity(
            db,
            "task.updated",
            f"Editó la tarea «{t.title}»",
            user=principal.user,
            area_id=p.area_id,
            project_id=p.id,
            ref_type="task",
            ref_id=t.id,
        )
    if "assignee_id" in changes and t.assignee_id and t.assignee_id != principal.id:
        notify(db, {t.assignee_id}, "task.assigned", f"Tarea asignada: {t.title}", link=f"/projects/{p.id}")
    if body.status and body.status != old_status and "done" in (body.status, old_status):
        enqueue(db, "memory.ingest", {"source_type": "task", "source_id": str(t.id)}, dedupe_key=f"memory:task:{t.id}")
    if body.status == "done" and old_status != "done":
        ai_tasks.cancel_open_runs(db, t, reason="terminada por una persona")
    emit(db, "task.changed", area_id=p.area_id, project_id=str(p.id), id=str(t.id))
    db.commit()
    return task_out(t, _latest_ai_runs(db, [t.id]).get(t.id))


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: uuid.UUID,
    version: int = Query(...),
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    t, p = _task(db, principal, task_id, "editor")
    if version != t.version:
        raise HTTPException(409, {"message": "La tarea cambió antes de borrarla", "current": task_out(t)})
    title = t.title
    ai_tasks.cancel_open_runs(db, t, reason="eliminada")
    db.delete(t)
    log_activity(
        db,
        "task.deleted",
        f"Eliminó la tarea «{title}»",
        user=principal.user,
        area_id=p.area_id,
        project_id=p.id,
        ref_type="task",
        ref_id=task_id,
    )
    emit(db, "task.changed", area_id=p.area_id, project_id=str(p.id), id=str(task_id))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- notas
class NoteIn(BaseModel):
    area_id: uuid.UUID
    project_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="note", pattern=NOTE_KINDS)
    content: str = Field(default="", max_length=500_000)


class NoteSave(BaseModel):
    base_version: int
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=500_000)
    force: bool = False  # el usuario resolvió un conflicto y confirma su versión


@router.get("/notes")
def list_notes(
    area_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    kind: str | None = None,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    ids = accessible_area_ids(db, principal)
    q = select(Note).where(Note.deleted_at.is_(None), Note.area_id.in_(ids))
    if area_id:
        q = q.where(Note.area_id == area_id)
    if project_id:
        q = q.where(Note.project_id == project_id)
    if kind:
        q = q.where(Note.kind == kind)
    return [note_out(n, content=False) for n in db.execute(q.order_by(Note.updated_at.desc()).limit(200)).scalars()]


@router.post("/notes")
def create_note(body: NoteIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    require_area(db, principal, body.area_id, "editor")
    if body.project_id:
        p = _project(db, principal, body.project_id, "editor")
        if p.area_id != body.area_id:
            raise HTTPException(422, "El proyecto no pertenece al área")
    n = Note(**body.model_dump(), created_by=principal.id, updated_by=principal.id)
    db.add(n)
    db.flush()
    db.add(NoteRevision(note_id=n.id, version=1, title=n.title, content=n.content, author_id=principal.id))
    track_note(db, n)
    log_activity(
        db,
        "note.created",
        f"Creó el documento «{n.title}»",
        user=principal.user,
        area_id=n.area_id,
        project_id=n.project_id,
        ref_type="note",
        ref_id=n.id,
    )
    emit(db, "note.changed", area_id=n.area_id, id=str(n.id), version=1)
    db.commit()
    return note_out(n)


@router.get("/notes/{note_id}")
def get_note(note_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    return note_out(_note(db, principal, note_id))


@router.put("/notes/{note_id}")
def save_note(
    note_id: uuid.UUID,
    body: NoteSave,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    """Guardado con concurrencia optimista. Si otra persona guardó antes, se intenta una fusión a tres
    bandas; si hay conflictos se devuelve 409 con ambas versiones para resolverlos sin perder nada."""
    n = db.execute(select(Note).where(Note.id == note_id).with_for_update()).scalar_one_or_none()
    if n is None or n.deleted_at is not None:
        raise HTTPException(404, "Documento no encontrado")
    require_area(db, principal, n.area_id, "editor")
    merged_from = None
    content, title = body.content, body.title
    if body.base_version != n.version and not body.force:
        base = db.execute(
            select(NoteRevision).where(NoteRevision.note_id == n.id, NoteRevision.version == body.base_version)
        ).scalar_one_or_none()
        if base is None:
            raise HTTPException(409, {"message": "Versión base desconocida", "current": note_out(n)})
        result = three_way_merge(base.content, body.content, n.content)
        if result.conflicts:
            raise HTTPException(
                409,
                {
                    "message": "Otra persona guardó cambios que se solapan con los suyos "
                    f"({result.conflicts} conflicto/s)",
                    "current": note_out(n),
                    "base_version": body.base_version,
                    "merged_with_markers": result.merged,
                },
            )
        content = result.merged
        title = body.title if body.title != base.title else n.title
        merged_from = n.version
    elif body.base_version != n.version and body.force:
        merged_from = n.version
    if content == n.content and title == n.title:
        return {**note_out(n), "merged": False, "unchanged": True}
    n.version += 1
    n.content = content
    n.title = title
    n.updated_by = principal.id
    db.add(NoteRevision(note_id=n.id, version=n.version, title=title, content=content, author_id=principal.id))
    track_note(db, n)
    # Una sola entrada de actividad por persona y documento cada 10 minutos (el autosave guarda a menudo).
    recent = db.execute(
        select(ActivityEvent.id)
        .where(
            ActivityEvent.ref_type == "note",
            ActivityEvent.ref_id == str(n.id),
            ActivityEvent.user_id == principal.id,
            ActivityEvent.kind == "note.updated",
            ActivityEvent.ts > func.now() - func.make_interval(0, 0, 0, 0, 0, 10),
        )
        .limit(1)
    ).first()
    if not recent:
        log_activity(
            db,
            "note.updated",
            f"Editó el documento «{n.title}»",
            user=principal.user,
            area_id=n.area_id,
            project_id=n.project_id,
            ref_type="note",
            ref_id=n.id,
        )
    emit(db, "note.changed", area_id=n.area_id, id=str(n.id), version=n.version, by=str(principal.id))
    db.commit()
    enqueue(
        db,
        "memory.ingest",
        {"source_type": "note", "source_id": str(n.id)},
        dedupe_key=f"memory:note:{n.id}",
        delay_seconds=120,
    )
    db.commit()
    return {**note_out(n), "merged": merged_from is not None, "merged_with_version": merged_from}


@router.get("/notes/{note_id}/revisions")
def note_revisions(
    note_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    n = _note(db, principal, note_id)
    rows = db.execute(
        select(NoteRevision, User)
        .outerjoin(User, User.id == NoteRevision.author_id)
        .where(NoteRevision.note_id == n.id)
        .order_by(NoteRevision.version.desc())
        .limit(200)
    ).all()
    return [
        {
            "version": r.version,
            "title": r.title,
            "created_at": iso(r.created_at),
            "author": u.display_name if u else None,
            "size": len(r.content),
        }
        for r, u in rows
    ]


@router.get("/notes/{note_id}/revisions/{version}")
def note_revision(
    note_id: uuid.UUID, version: int, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    n = _note(db, principal, note_id)
    r = db.execute(
        select(NoteRevision).where(NoteRevision.note_id == n.id, NoteRevision.version == version)
    ).scalar_one_or_none()
    if r is None:
        raise HTTPException(404, "Revisión no encontrada")
    return {"version": r.version, "title": r.title, "content": r.content, "created_at": iso(r.created_at)}


@router.delete("/notes/{note_id}")
def delete_note(note_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    n = _note(db, principal, note_id, "editor")
    n.deleted_at = now_utc()
    log_activity(
        db,
        "note.deleted",
        f"Eliminó el documento «{n.title}»",
        user=principal.user,
        area_id=n.area_id,
        project_id=n.project_id,
        ref_type="note",
        ref_id=n.id,
    )
    emit(db, "note.changed", area_id=n.area_id, id=str(n.id))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- comentarios
class CommentIn(BaseModel):
    target_type: str = Field(pattern="^(project|task|note|document|decision)$")
    target_id: uuid.UUID
    body: str = Field(min_length=1, max_length=10000)


def _target_area(db: Session, target_type: str, target_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID | None]:
    if target_type == "project":
        p = db.get(Project, target_id)
        if p:
            return p.area_id, p.id
    elif target_type == "task":
        t = db.get(Task, target_id)
        if t:
            p = db.get(Project, t.project_id)
            return p.area_id, p.id
    elif target_type == "note":
        n = db.get(Note, target_id)
        if n and n.deleted_at is None:
            return n.area_id, n.project_id
    elif target_type == "document":
        d = db.get(Document, target_id)
        if d and d.deleted_at is None:
            return d.area_id, d.project_id
    elif target_type == "decision":
        d = db.get(Decision, target_id)
        if d:
            return d.area_id, d.project_id
    raise HTTPException(404, "Elemento no encontrado")


@router.get("/comments")
def list_comments(
    target_type: str,
    target_id: uuid.UUID,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    area_id, _ = _target_area(db, target_type, target_id)
    require_area(db, principal, area_id)
    rows = db.execute(
        select(Comment, User)
        .outerjoin(User, User.id == Comment.author_id)
        .where(Comment.target_type == target_type, Comment.target_id == target_id, Comment.deleted_at.is_(None))
        .order_by(Comment.created_at)
    ).all()
    return [
        {
            "id": str(c.id),
            "body": c.body,
            "author": user_public(u) if u else None,
            "created_at": iso(c.created_at),
            "edited_at": iso(c.edited_at),
        }
        for c, u in rows
    ]


@router.post("/comments")
def add_comment(body: CommentIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    area_id, project_id = _target_area(db, body.target_type, body.target_id)
    require_area(db, principal, area_id, "viewer")
    c = Comment(
        area_id=area_id, target_type=body.target_type, target_id=body.target_id, author_id=principal.id, body=body.body
    )
    db.add(c)
    db.flush()
    log_activity(
        db,
        "comment.created",
        f"Comentó: {body.body[:120]}",
        user=principal.user,
        area_id=area_id,
        project_id=project_id,
        ref_type=body.target_type,
        ref_id=body.target_id,
    )
    emit(db, "comment.changed", area_id=area_id, target_type=body.target_type, target_id=str(body.target_id))
    db.commit()
    return {"id": str(c.id)}


@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    c = db.get(Comment, comment_id)
    if c is None or c.deleted_at:
        raise HTTPException(404, "Comentario no encontrado")
    if c.author_id != principal.id:
        require_area(db, principal, c.area_id, "owner")
    c.deleted_at = now_utc()
    emit(db, "comment.changed", area_id=c.area_id, target_type=c.target_type, target_id=str(c.target_id))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- decisiones
class DecisionIn(BaseModel):
    area_id: uuid.UUID
    project_id: uuid.UUID | None = None
    title: str = Field(min_length=2, max_length=200)
    body: str = Field(default="", max_length=20000)


class DecisionStatus(BaseModel):
    status: str = Field(pattern="^(proposed|approved|rejected|superseded)$")


@router.get("/decisions")
def list_decisions(
    area_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    ids = accessible_area_ids(db, principal)
    q = select(Decision).where(Decision.area_id.in_(ids))
    if area_id:
        q = q.where(Decision.area_id == area_id)
    if project_id:
        q = q.where(Decision.project_id == project_id)
    return [decision_out(d) for d in db.execute(q.order_by(Decision.created_at.desc()).limit(200)).scalars()]


@router.post("/decisions")
def create_decision(
    body: DecisionIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    require_area(db, principal, body.area_id, "editor")
    if body.project_id:
        p = _project(db, principal, body.project_id)
        if p.area_id != body.area_id:
            raise HTTPException(422, "El proyecto no pertenece al área")
    d = Decision(**body.model_dump(), created_by=principal.id)
    db.add(d)
    db.flush()
    log_activity(
        db,
        "decision.created",
        f"Propuso la decisión «{d.title}»",
        user=principal.user,
        area_id=d.area_id,
        project_id=d.project_id,
        ref_type="decision",
        ref_id=d.id,
    )
    emit(db, "decision.changed", area_id=d.area_id, id=str(d.id))
    db.commit()
    return decision_out(d)


@router.post("/decisions/{decision_id}/status")
def set_decision_status(
    decision_id: uuid.UUID,
    body: DecisionStatus,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    d = db.get(Decision, decision_id)
    if d is None:
        raise HTTPException(404, "Decisión no encontrada")
    require_area(db, principal, d.area_id, "owner" if body.status in ("approved", "rejected") else "editor")
    d.status = body.status
    if body.status in ("approved", "rejected"):
        d.decided_by = principal.id
        d.decided_at = now_utc()
    label = {"approved": "Aprobó", "rejected": "Rechazó", "superseded": "Marcó como sustituida", "proposed": "Reabrió"}[
        body.status
    ]
    log_activity(
        db,
        f"decision.{body.status}",
        f"{label} la decisión «{d.title}»",
        user=principal.user,
        area_id=d.area_id,
        project_id=d.project_id,
        ref_type="decision",
        ref_id=d.id,
    )
    emit(db, "decision.changed", area_id=d.area_id, id=str(d.id))
    db.commit()
    if body.status in ("approved", "superseded", "rejected"):
        enqueue(
            db,
            "memory.ingest",
            {"source_type": "decision", "source_id": str(d.id)},
            dedupe_key=f"memory:decision:{d.id}",
        )
        db.commit()
    return decision_out(d)


# --------------------------------------------------------------------------- actividad
@router.get("/activity")
def activity(
    area_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    before: int | None = None,
    limit: int = 50,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    ids = accessible_area_ids(db, principal)
    if not ids:
        return []
    q = select(ActivityEvent).where(ActivityEvent.area_id.in_(ids))
    if area_id:
        q = q.where(ActivityEvent.area_id == area_id)
    if project_id:
        q = q.where(ActivityEvent.project_id == project_id)
    if user_id:
        q = q.where(ActivityEvent.user_id == user_id)
    if before:
        q = q.where(ActivityEvent.id < before)
    rows = db.execute(q.order_by(ActivityEvent.id.desc()).limit(min(limit, 200))).scalars()
    return [activity_out(e) for e in rows]


@router.get("/areas/{area_id}/summary")
def get_area_summary(
    area_id: uuid.UUID,
    days: int = 7,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    require_area(db, principal, area_id)
    return area_summary(db, area_id, max(1, min(days, 90)))
