"""Conexión con Google Drive (OAuth), estado de sincronización, conflictos y permisos de carpetas."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima import audit
from wadima.api.serial import iso
from wadima.config import get_settings
from wadima.db import get_db
from wadima.drive import oauth
from wadima.drive import providers as _providers  # noqa: F401 - registra proveedores de contenido
from wadima.drive.client import DriveAuthError, DriveError
from wadima.drive.sync import (
    client_for,
    ensure_structure,
    reconcile,
    resolve_conflict,
    sync_counts,
    workspace_connection,
)
from wadima.models import DriveConnection, DriveFile, SyncLog, User
from wadima.realtime import emit
from wadima.security.auth import Principal, client_ip, now_utc, require_admin, require_password_ok
from wadima.security.perms import accessible_area_ids
from wadima.services import jobs

router = APIRouter(prefix="/api/drive", tags=["drive"])


def conn_out(c: DriveConnection | None) -> dict | None:
    if c is None:
        return None
    return {
        "id": str(c.id),
        "scope": c.scope,
        "status": c.status,
        "google_email": c.google_email,
        "google_name": c.google_name,
        "granted_scopes": c.granted_scopes,
        "root_folder_id": c.root_folder_id,
        "folders": {k: v for k, v in (c.folders or {}).items() if "/" not in k},
        "last_error": c.last_error,
        "last_checked_at": iso(c.last_checked_at),
        "last_sync_at": iso(c.last_sync_at),
        "created_at": iso(c.created_at),
        "connected_by": str(c.user_id),
    }


def file_out(f: DriveFile) -> dict:
    return {
        "id": str(f.id),
        "local_kind": f.local_kind,
        "local_id": f.local_id,
        "area_id": str(f.area_id) if f.area_id else None,
        "folder_key": f.folder_key,
        "name": f.name,
        "mime": f.mime,
        "sync_state": f.sync_state,
        "drive_file_id": f.drive_file_id,
        "drive_version": f.drive_version,
        "drive_modified_time": f.drive_modified_time,
        "local_md5": f.local_md5,
        "drive_md5": f.drive_md5,
        "attempts": f.attempts,
        "last_error": f.last_error,
        "conflict": f.conflict,
        "last_synced_at": iso(f.last_synced_at),
        "updated_at": iso(f.updated_at),
        "web_link": f"https://drive.google.com/file/d/{f.drive_file_id}/view" if f.drive_file_id else None,
    }


@router.get("/summary")
def summary(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    return drive_summary(db)


def drive_summary(db: Session) -> dict:
    counts = sync_counts(db)
    conn = workspace_connection(db, include_error=True)
    if not oauth.configured() and conn is None:
        status = "not_configured"
    elif conn is None or conn.status != "connected":
        status = "disconnected"
    elif counts.get("conflict") or counts.get("remote_deleted"):
        status = "conflict"
    elif counts.get("error"):
        status = "error"
    elif counts.get("pending") or counts.get("uploading"):
        status = "pending"
    else:
        status = "synced"
    return {
        "configured": oauth.configured(),
        "connected": bool(conn and conn.status == "connected"),
        "status": status,
        "counts": counts,
        "last_sync_at": iso(conn.last_sync_at) if conn else None,
        "last_error": conn.last_error if conn else "",
    }


@router.get("/status")
def status(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    s = get_settings()
    conn = workspace_connection(db, include_error=True)
    return {
        "configured": oauth.configured(),
        "redirect_uri": s.google_redirect_uri,
        "scope": oauth.scopes(),
        "connection": conn_out(conn),
        "counts": sync_counts(db),
        "can_manage": principal.is_admin,
    }


@router.post("/connect")
def connect(admin: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    if not oauth.configured():
        raise HTTPException(
            400,
            "Faltan WADIMA_GOOGLE_CLIENT_ID y WADIMA_GOOGLE_CLIENT_SECRET en el .env del servidor "
            "(credenciales OAuth de un proyecto de Google Cloud con la API de Drive habilitada).",
        )
    url = oauth.start_flow(db, admin.id)
    db.commit()
    return {"auth_url": url}


@router.get("/oauth/callback")
def oauth_callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    target = f"{get_settings().public_origin}/connections"
    if error:
        return RedirectResponse(f"{target}?drive=denied", status_code=303)
    try:
        _, verifier = oauth.consume_state(db, state, principal.id)
        db.commit()
    except DriveAuthError:
        db.rollback()
        return RedirectResponse(f"{target}?drive=invalid_state", status_code=303)
    row = db.execute(
        select(DriveConnection)
        .where(DriveConnection.scope == "workspace")
        .order_by(DriveConnection.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    try:
        tokens = oauth.exchange_code(code, verifier)
        conn = row or DriveConnection(scope="workspace", user_id=principal.id)
        conn.user_id = principal.id
        oauth.store_tokens(conn, tokens)
        conn.status = "connected"
        conn.last_error = ""
        if row is None:
            db.add(conn)
        db.flush()
        client = client_for(conn)
        try:
            about = client.about().get("user", {})
            conn.google_email = about.get("emailAddress")
            conn.google_name = about.get("displayName")
            ensure_structure(client, conn)
            conn.changes_page_token = client.start_page_token()
        finally:
            client.close()
        conn.last_checked_at = now_utc()
        audit.record(
            db,
            "drive.connected",
            actor_id=principal.id,
            target_type="drive_connection",
            target_id=conn.id,
            ip=client_ip(request),
            data={"google_email": conn.google_email},
        )
        emit(db, "drive.changed", everyone=True)
        db.commit()
        _requeue_pending(db)
        return RedirectResponse(f"{target}?drive=connected", status_code=303)
    except (DriveError, KeyError) as exc:
        db.rollback()
        audit.record(
            db, "drive.connect_failed", actor_id=principal.id, ip=client_ip(request), data={"error": str(exc)[:300]}
        )
        db.commit()
        return RedirectResponse(f"{target}?drive=error", status_code=303)


def _requeue_pending(db: Session) -> None:
    for row in db.execute(select(DriveFile).where(DriveFile.sync_state.in_(("pending", "error")))).scalars():
        jobs.enqueue(db, "drive.sync", {"row_id": str(row.id)}, dedupe_key=f"drive:{row.id}", max_attempts=8)
    db.commit()


@router.post("/test")
def test_connection(admin: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Prueba real: identidad, estructura de carpetas y ciclo subir → verificar md5 → descargar → borrar."""
    conn = workspace_connection(db, include_error=True)
    if conn is None:
        raise HTTPException(400, "No hay conexión con Google Drive")
    import hashlib

    steps: list[dict] = []
    client = client_for(conn)
    try:
        about = client.about().get("user", {})
        steps.append({"step": "identidad", "ok": True, "detail": about.get("emailAddress")})
        folders = ensure_structure(client, conn)
        steps.append({"step": "carpetas", "ok": True, "detail": f"{len(folders) - 1} carpetas en WADIMA/"})
        probe = f"wadima-probe {now_utc().isoformat()}".encode()
        meta = client.upload(".wadima-probe.txt", folders["Workspace"], "text/plain", probe, {"wadimaRef": "probe"})
        ok = meta.get("md5Checksum") == hashlib.md5(probe, usedforsecurity=False).hexdigest()
        steps.append({"step": "subida y verificación md5", "ok": ok, "detail": meta.get("id")})
        back = client.download(meta["id"])
        steps.append({"step": "descarga", "ok": back == probe, "detail": f"{len(back)} bytes"})
        client.delete(meta["id"])
        steps.append({"step": "limpieza", "ok": True, "detail": "archivo de prueba eliminado"})
        conn.status = "connected"
        conn.last_error = ""
        conn.google_email = about.get("emailAddress") or conn.google_email
    except DriveError as exc:
        steps.append({"step": "error", "ok": False, "detail": str(exc)})
        conn.status = "error" if isinstance(exc, DriveAuthError) else conn.status
        conn.last_error = str(exc)
    finally:
        client.close()
    conn.last_checked_at = now_utc()
    emit(db, "drive.changed", everyone=True)
    db.commit()
    return {"ok": all(s["ok"] for s in steps), "steps": steps}


@router.post("/disconnect")
def disconnect(request: Request, admin: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    conn = workspace_connection(db, include_error=True)
    if conn is None:
        raise HTTPException(400, "No hay conexión activa")
    revoked = False
    try:
        revoked = oauth.revoke(oauth.load_tokens(conn))
    except (DriveError, ValueError):
        revoked = False
    conn.token_encrypted = None
    conn.status = "disconnected"
    audit.record(
        db,
        "drive.disconnected",
        actor_id=admin.id,
        target_type="drive_connection",
        target_id=conn.id,
        ip=client_ip(request),
        data={"revoked_at_google": revoked},
    )
    emit(db, "drive.changed", everyone=True)
    db.commit()
    return {"ok": True, "revoked_at_google": revoked}


@router.get("/files")
def files(
    state: str | None = None,
    local_kind: str | None = None,
    local_id: str | None = None,
    limit: int = 200,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    ids = accessible_area_ids(db, principal)
    q = select(DriveFile).where((DriveFile.area_id.in_(ids)) | (DriveFile.area_id.is_(None)))
    if state:
        q = q.where(DriveFile.sync_state == state)
    if local_kind:
        q = q.where(DriveFile.local_kind == local_kind)
    if local_id:
        q = q.where(DriveFile.local_id == local_id)
    if not principal.is_admin:
        q = q.where(DriveFile.local_kind.not_in(("backup", "audit_anchor")))
    rows = db.execute(q.order_by(DriveFile.updated_at.desc()).limit(min(limit, 500))).scalars()
    return [file_out(f) for f in rows]


def _file(db: Session, principal: Principal, file_id: uuid.UUID) -> DriveFile:
    f = db.get(DriveFile, file_id)
    if f is None or (f.area_id and f.area_id not in accessible_area_ids(db, principal)):
        raise HTTPException(404, "Archivo no encontrado")
    return f


@router.post("/files/{file_id}/retry")
def retry(file_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    f = _file(db, principal, file_id)
    if f.sync_state == "remote_deleted":
        f.drive_file_id = None  # volver a crear el archivo en Drive
        f.base_md5 = None
    if f.sync_state in ("error", "pending", "remote_deleted", "uploading"):
        f.sync_state = "pending"
        f.next_attempt_at = now_utc()
        jobs.enqueue(db, "drive.sync", {"row_id": str(f.id)}, dedupe_key=f"drive:{f.id}", max_attempts=8)
        emit(db, "sync.changed", everyone=True, row_id=str(f.id), state="pending")
    db.commit()
    return file_out(f)


class ResolveIn(BaseModel):
    strategy: str = Field(pattern="^(keep_local|keep_remote|keep_both)$")


@router.post("/files/{file_id}/resolve")
def resolve(
    file_id: uuid.UUID,
    body: ResolveIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    f = _file(db, principal, file_id)
    if f.sync_state != "conflict":
        raise HTTPException(409, "El archivo no está en conflicto")
    try:
        result = resolve_conflict(db, f, body.strategy)
    except DriveError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    audit.record(
        db,
        "drive.conflict_resolved",
        actor_id=principal.id,
        target_type="drive_file",
        target_id=f.id,
        area_id=f.area_id,
        ip=client_ip(request),
        data={"strategy": body.strategy},
    )
    db.commit()
    return result


@router.post("/reconcile")
def run_reconcile(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    result = reconcile(db)
    db.commit()
    return result


@router.get("/log")
def sync_log(limit: int = 100, admin: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(
        select(SyncLog, DriveFile.name)
        .outerjoin(DriveFile, DriveFile.id == SyncLog.drive_file_id)
        .order_by(SyncLog.id.desc())
        .limit(min(limit, 500))
    ).all()
    return [
        {"id": r.id, "ts": iso(r.ts), "op": r.op, "result": r.result, "detail": r.detail, "file": name}
        for r, name in rows
    ]


@router.get("/permissions")
def permissions(admin: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    """Comprueba qué socios tienen acceso a la carpeta WADIMA (no se asume una unidad compartida)."""
    conn = workspace_connection(db)
    if conn is None or not conn.root_folder_id:
        raise HTTPException(400, "Google Drive no está conectado")
    client = client_for(conn)
    try:
        perms = client.permissions(conn.root_folder_id)
    except DriveError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        client.close()
    emails = {(p.get("emailAddress") or "").lower(): p for p in perms}
    users = db.execute(select(User).where(User.is_active).order_by(User.created_at, User.username)).scalars()
    return {
        "permissions": perms,
        "partners": [
            {
                "user_id": str(u.id),
                "display_name": u.display_name,
                "email": u.email,
                "access": emails.get((u.email or "").lower(), {}).get("role"),
            }
            for u in users
        ],
    }


@router.post("/share/{user_id}")
def share_with(
    user_id: uuid.UUID, request: Request, admin: Principal = Depends(require_admin), db: Session = Depends(get_db)
):
    conn = workspace_connection(db)
    user = db.get(User, user_id)
    if conn is None or not conn.root_folder_id:
        raise HTTPException(400, "Google Drive no está conectado")
    if user is None or not user.email:
        raise HTTPException(422, "El socio debe indicar su correo de Google en Configuración")
    client = client_for(conn)
    try:
        perm = client.share(conn.root_folder_id, user.email, "writer")
    except DriveError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        client.close()
    audit.record(
        db,
        "drive.shared",
        actor_id=admin.id,
        target_type="user",
        target_id=user.id,
        ip=client_ip(request),
        data={"email": user.email, "role": "writer"},
    )
    db.commit()
    return perm


@router.post("/backup")
def backup_now(admin: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    from wadima.drive.providers import track_audit_anchor, track_backup

    track_backup(db)
    track_audit_anchor(db)
    db.commit()
    return {"ok": True}
