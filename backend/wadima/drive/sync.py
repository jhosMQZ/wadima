"""Motor de sincronización con Google Drive.

- Cada objeto local que debe persistir en Drive tiene una fila DriveFile (ID estable de Drive, md5 base).
- Las subidas se verifican comparando el md5 que calcula Google con el local.
- Cambios simultáneos local/remoto -> estado 'conflict' (nunca se sobrescribe en silencio).
- Fallos de red -> reintentos con espera exponencial; el estado 'error'/'pending' queda visible.
- Interrupciones -> las sesiones de subida reanudable se guardan y la búsqueda por appProperties
  evita duplicados si el proceso cayó tras subir y antes de registrar el ID.
- Reconciliación periódica con changes.list (las notificaciones push requieren HTTPS público).
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wadima.db import SessionLocal
from wadima.drive.client import DriveAuthError, DriveClient, DriveError
from wadima.drive.oauth import load_tokens, store_tokens
from wadima.models import Area, AreaMember, DriveConnection, DriveFile, SyncLog, User
from wadima.realtime import emit
from wadima.security.auth import now_utc
from wadima.services import jobs

log = logging.getLogger("wadima.drive.sync")

ROOT_NAME = "WADIMA"
FOLDERS = ["Workspace", "Areas", "Projects", "Library", "Processed", "Markdown", "Memory", "Reports", "Backups"]

Provider = Callable[[Session, DriveFile], tuple[bytes, str]]
Applier = Callable[[Session, DriveFile, bytes, dict], None]
PROVIDERS: dict[str, Provider] = {}
REMOTE_APPLIERS: dict[str, Applier] = {}


def provider(kind: str) -> Callable[[Provider], Provider]:
    def deco(fn: Provider) -> Provider:
        PROVIDERS[kind] = fn
        return fn

    return deco


def remote_applier(kind: str) -> Callable[[Applier], Applier]:
    def deco(fn: Applier) -> Applier:
        REMOTE_APPLIERS[kind] = fn
        return fn

    return deco


def digests(content: bytes) -> tuple[str, str]:
    return hashlib.sha256(content).hexdigest(), hashlib.md5(content, usedforsecurity=False).hexdigest()


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip()[:200] or "archivo"


# --------------------------------------------------------------------------- conexión
def workspace_connection(db: Session, include_error: bool = False) -> DriveConnection | None:
    states = ("connected", "error") if include_error else ("connected",)
    return db.execute(
        select(DriveConnection)
        .where(DriveConnection.scope == "workspace", DriveConnection.status.in_(states))
        .order_by(DriveConnection.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def client_for(conn: DriveConnection) -> DriveClient:
    conn_id = conn.id

    def persist(tokens):  # los tokens renovados se guardan aunque el trabajo falle después
        s = SessionLocal()
        try:
            c = s.get(DriveConnection, conn_id)
            if c is not None:
                store_tokens(c, tokens)
                s.commit()
        finally:
            s.close()

    return DriveClient(load_tokens(conn), on_refresh=persist)


def ensure_structure(client: DriveClient, conn: DriveConnection) -> dict[str, str]:
    folders = dict(conn.folders or {})
    root = folders.get("_root")
    if root:
        meta = client.get(root)
        if meta is None or meta.get("trashed"):
            folders = {}
            root = None
    if not root:
        root = client.ensure_folder(ROOT_NAME, None)
        folders["_root"] = root
    for name in FOLDERS:
        if name not in folders:
            folders[name] = client.ensure_folder(name, root)
    conn.root_folder_id = root
    conn.folders = folders
    return folders


def folder_id(client: DriveClient, conn: DriveConnection, key: str) -> str:
    """Resuelve rutas como 'Library/finanzas' creando subcarpetas bajo demanda (cacheadas)."""
    folders = conn.folders or {}
    if key in folders:
        return folders[key]
    if not folders.get("_root"):
        folders = ensure_structure(client, conn)
    parts = key.split("/")
    current_key = parts[0]
    parent = folders.get(current_key) or client.ensure_folder(current_key, folders["_root"])
    folders = {**folders, current_key: parent}
    for part in parts[1:]:
        current_key = f"{current_key}/{part}"
        if current_key not in folders:
            folders[current_key] = client.ensure_folder(safe_name(part), parent)
        parent = folders[current_key]
    conn.folders = folders
    return parent


# --------------------------------------------------------------------------- registro de objetos a sincronizar
def track(
    db: Session,
    kind: str,
    local_id: str | uuid.UUID,
    *,
    area_id: uuid.UUID | None,
    folder_key: str,
    name: str,
    mime: str,
    content: bytes,
    delay_seconds: int = 0,
) -> DriveFile:
    """Marca un objeto local como pendiente de subir si su contenido cambió."""
    sha, md5 = digests(content)
    row = db.execute(
        select(DriveFile).where(DriveFile.local_kind == kind, DriveFile.local_id == str(local_id)).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        row = DriveFile(
            local_kind=kind,
            local_id=str(local_id),
            area_id=area_id,
            folder_key=folder_key,
            name=safe_name(name),
            mime=mime,
            local_sha256=sha,
            local_md5=md5,
            sync_state="pending",
        )
        db.add(row)
        db.flush()
    else:
        if row.local_sha256 == sha and row.sync_state in ("synced", "pending", "uploading"):
            if row.name != safe_name(name):
                row.name = safe_name(name)
            return row
        row.local_sha256, row.local_md5 = sha, md5
        row.name = safe_name(name)
        if row.sync_state not in ("conflict", "remote_deleted"):
            row.sync_state = "pending"
        row.next_attempt_at = now_utc()
    jobs.enqueue(
        db,
        "drive.sync",
        {"row_id": str(row.id)},
        dedupe_key=f"drive:{row.id}",
        delay_seconds=delay_seconds,
        max_attempts=8,
    )
    emit(db, "sync.changed", everyone=True, row_id=str(row.id), state=row.sync_state)
    return row


def _log(db: Session, row: DriveFile | None, op: str, result: str, detail: str = "") -> None:
    db.add(SyncLog(drive_file_id=row.id if row else None, op=op, result=result, detail=detail[:2000]))


def _admins(db: Session) -> set[uuid.UUID]:
    return set(db.execute(select(User.id).where(User.role == "admin", User.is_active)).scalars())


def _area_people(db: Session, area_id: uuid.UUID | None) -> set[uuid.UUID]:
    people = _admins(db)
    if area_id:
        people |= set(db.execute(select(AreaMember.user_id).where(AreaMember.area_id == area_id)).scalars())
    return people


# --------------------------------------------------------------------------- sincronización de una fila
@jobs.register("drive.sync", "drive_sync")
def handle_sync(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    return sync_row(db, uuid.UUID(payload["row_id"]))


def _row_lock_key(row_id: uuid.UUID) -> int:
    return int.from_bytes(row_id.bytes[:8], "big", signed=True)


def sync_row(db: Session, row_id: uuid.UUID) -> dict[str, Any]:
    # Exclusión por fila con un lock consultivo (no FOR UPDATE): así la sesión de subida reanudable puede
    # guardarse desde otra transacción sin bloquearse contra esta.
    if not db.execute(select(func.pg_try_advisory_xact_lock(_row_lock_key(row_id)))).scalar_one():
        raise jobs.RetryLater("Otra sincronización de este archivo está en curso", 30)
    row = db.get(DriveFile, row_id)
    if row is None:
        return {"skipped": "fila inexistente"}
    if row.sync_state in ("conflict", "remote_deleted"):
        return {"skipped": row.sync_state}
    conn = workspace_connection(db)
    if conn is None:
        raise jobs.RetryLater("Google Drive no está conectado; el cambio queda pendiente", 300)
    try:
        prov = PROVIDERS.get(row.local_kind)
        if prov is None:
            raise LookupError(f"Sin proveedor de contenido para '{row.local_kind}'")
        content, mime = prov(db, row)
    except LookupError as exc:
        row.sync_state = "error"
        row.last_error = f"Objeto local no disponible: {exc}"
        _log(db, row, "read_local", "error", row.last_error)
        db.commit()
        return {"error": row.last_error}
    sha, md5 = digests(content)
    row.local_sha256, row.local_md5, row.mime = sha, md5, mime
    client = client_for(conn)
    props = {"wadimaRef": f"{row.local_kind}:{row.local_id}", "wadimaSha256": sha}
    row_pk = row.id

    def save_session(uri: str) -> None:
        s = SessionLocal()
        try:
            r = s.get(DriveFile, row_pk)
            if r is not None:
                r.upload_session_uri = uri
                s.commit()
        finally:
            s.close()

    try:
        row.sync_state = "uploading"
        parent = folder_id(client, conn, row.folder_key)
        meta: dict | None = None
        op = "upload"
        if not row.drive_file_id:
            existing = client.find_by_property("wadimaRef", props["wadimaRef"])
            if existing:
                # Recuperación: la subida anterior llegó a Drive pero no se registró localmente.
                found = existing[0]
                row.drive_file_id = found["id"]
                _log(db, row, "recover", "ok", f"Archivo existente adoptado {found['id']}")
                if found.get("md5Checksum") == md5:
                    meta, op = found, "adopt"
                else:
                    meta = client.update_content(
                        found["id"], mime, content, props, row.upload_session_uri, save_session
                    )
                    op = "update"
            else:
                meta = client.upload(row.name, parent, mime, content, props, row.upload_session_uri, save_session)
        else:
            remote = client.get(row.drive_file_id)
            if remote is None or remote.get("trashed"):
                row.sync_state = "remote_deleted"
                row.last_error = "El archivo fue eliminado o enviado a la papelera en Drive"
                _log(db, row, "check", "conflict", row.last_error)
                emit(db, "sync.changed", everyone=True, row_id=str(row.id), state=row.sync_state)
                db.commit()
                return {"state": row.sync_state}
            remote_md5 = remote.get("md5Checksum")
            remote_changed = remote_md5 != row.base_md5
            local_changed = md5 != row.base_md5
            if remote_changed and remote_md5 == md5:
                meta, op = remote, "noop"
            elif remote_changed and local_changed:
                return _mark_conflict(db, row, remote, md5)
            elif remote_changed:
                applier = REMOTE_APPLIERS.get(row.local_kind)
                if applier is None:
                    return _mark_conflict(db, row, remote, md5)
                data = client.download(row.drive_file_id)
                if digests(data)[1] != remote_md5:
                    raise DriveError("La descarga no coincide con el md5 de Drive", retryable=True)
                applier(db, row, data, remote)
                row.local_sha256, row.local_md5 = digests(data)
                md5 = row.local_md5
                meta, op = remote, "download"
            elif local_changed:
                meta = client.update_content(
                    row.drive_file_id, mime, content, props, row.upload_session_uri, save_session
                )
                op = "update"
            else:
                meta, op = remote, "noop"
        if meta.get("md5Checksum") != md5:
            raise DriveError(
                f"Verificación fallida: md5 de Drive {meta.get('md5Checksum')} ≠ local {md5}", retryable=True
            )
        row.drive_file_id = meta["id"]
        row.drive_parent_id = (meta.get("parents") or [parent])[0]
        row.drive_md5 = meta.get("md5Checksum")
        row.drive_version = str(meta.get("version", ""))
        row.drive_modified_time = meta.get("modifiedTime")
        row.base_md5 = md5
        row.sync_state = "synced"
        row.attempts = 0
        row.last_error = ""
        row.upload_session_uri = None
        row.connection_id = conn.id
        row.last_synced_at = now_utc()
        _log(db, row, op, "ok", f"{row.name} ({meta['id']})")
        emit(db, "sync.changed", everyone=True, row_id=str(row.id), state="synced")
        db.commit()
        return {"state": "synced", "drive_file_id": row.drive_file_id, "op": op}
    except DriveAuthError as exc:
        db.rollback()
        _record_failure(row_pk, "pending", str(exc), auth_failed_conn=conn.id)
        raise jobs.RetryLater(str(exc), 600) from exc
    except DriveError as exc:
        db.rollback()
        _record_failure(row_pk, "error", str(exc))
        raise
    finally:
        client.close()


def _record_failure(row_id: uuid.UUID, state: str, message: str, auth_failed_conn: uuid.UUID | None = None) -> None:
    s = SessionLocal()
    try:
        r = s.get(DriveFile, row_id)
        if r is not None:
            r.sync_state = state
            r.attempts += 1
            r.last_error = message[:2000]
            r.next_attempt_at = now_utc() + timedelta(seconds=min(3600, 15 * 2 ** min(r.attempts, 8)))
            _log(s, r, "sync", "retry", message)
            emit(s, "sync.changed", everyone=True, row_id=str(r.id), state=state)
        if auth_failed_conn:
            c = s.get(DriveConnection, auth_failed_conn)
            if c is not None:
                c.status = "error"
                c.last_error = message[:2000]
                emit(s, "drive.changed", everyone=True)
        s.commit()
    finally:
        s.close()


def _mark_conflict(db: Session, row: DriveFile, remote: dict, local_md5: str) -> dict[str, Any]:
    from wadima.activity import notify

    row.sync_state = "conflict"
    row.conflict = {
        "remote_md5": remote.get("md5Checksum"),
        "remote_version": str(remote.get("version", "")),
        "remote_modified": remote.get("modifiedTime"),
        "local_md5": local_md5,
        "base_md5": row.base_md5,
        "detected_at": now_utc().isoformat(),
    }
    row.last_error = "Cambios simultáneos en WADIMA y en Drive"
    _log(db, row, "check", "conflict", row.last_error)
    notify(
        db,
        _area_people(db, row.area_id),
        "sync.conflict",
        f"Conflicto de sincronización: {row.name}",
        "El archivo cambió en WADIMA y en Google Drive. Elija qué versión conservar.",
        "/connections",
    )
    emit(db, "sync.changed", everyone=True, row_id=str(row.id), state="conflict")
    db.commit()
    return {"state": "conflict"}


# --------------------------------------------------------------------------- resolución de conflictos
def resolve_conflict(db: Session, row: DriveFile, strategy: str) -> dict[str, Any]:
    """keep_local: sube la versión de WADIMA (Drive conserva la anterior en su historial de versiones).
    keep_remote: importa la versión de Drive como versión local (si el tipo lo admite).
    keep_both: guarda la versión local como un archivo nuevo en Drive y deja el remoto intacto."""
    conn = workspace_connection(db)
    if conn is None:
        raise DriveError("Google Drive no está conectado")
    client = client_for(conn)
    try:
        if strategy == "keep_local":
            remote = client.get(row.drive_file_id) if row.drive_file_id else None
            if remote is None or remote.get("trashed"):
                row.drive_file_id = None  # se vuelve a crear
            else:
                row.base_md5 = remote.get("md5Checksum")  # se acepta sobrescribir esa versión
        elif strategy == "keep_remote":
            applier = REMOTE_APPLIERS.get(row.local_kind)
            if applier is None:
                raise DriveError("Este tipo de archivo se genera en WADIMA y no admite importar la versión remota")
            remote = client.get(row.drive_file_id)
            if remote is None:
                raise DriveError("El archivo remoto ya no existe")
            data = client.download(row.drive_file_id)
            applier(db, row, data, remote)
            row.local_sha256, row.local_md5 = digests(data)
            row.base_md5 = remote.get("md5Checksum")
        elif strategy == "keep_both":
            copy = DriveFile(
                local_kind=row.local_kind,
                local_id=f"{row.local_id}:copia:{now_utc():%Y%m%d%H%M%S}",
                area_id=row.area_id,
                folder_key=row.folder_key,
                name=f"{row.name} (copia WADIMA)",
                mime=row.mime,
                local_sha256=row.local_sha256,
                local_md5=row.local_md5,
                sync_state="synced",
            )
            prov = PROVIDERS[row.local_kind]
            content, mime = prov(db, row)
            meta = client.upload(
                copy.name,
                folder_id(client, conn, row.folder_key),
                mime,
                content,
                {"wadimaRef": f"{copy.local_kind}:{copy.local_id}"},
            )
            copy.drive_file_id, copy.drive_md5, copy.base_md5 = meta["id"], meta.get("md5Checksum"), row.local_md5
            copy.last_synced_at = now_utc()
            db.add(copy)
            # La fila original pasa a seguir a la versión remota.
            applier = REMOTE_APPLIERS.get(row.local_kind)
            remote = client.get(row.drive_file_id)
            if applier and remote:
                applier(db, row, client.download(row.drive_file_id), remote)
            row.base_md5 = remote.get("md5Checksum") if remote else row.base_md5
        else:
            raise ValueError("Estrategia desconocida")
    finally:
        client.close()
    row.sync_state = "pending"
    row.conflict = {}
    row.last_error = ""
    _log(db, row, "resolve", "ok", strategy)
    jobs.enqueue(db, "drive.sync", {"row_id": str(row.id)}, dedupe_key=f"drive:{row.id}", max_attempts=8)
    emit(db, "sync.changed", everyone=True, row_id=str(row.id), state="pending")
    return {"state": "pending", "strategy": strategy}


# --------------------------------------------------------------------------- reconciliación periódica
def reconcile(db: Session) -> dict[str, Any]:
    conn = workspace_connection(db)
    stats = sync_counts(db)
    if conn is None:
        jobs.set_agent(
            db,
            "drive_sync",
            "Sincronizador de Drive",
            "idle",
            "",
            stats={"connected": False, "pending": stats.get("pending", 0)},
        )
        return {"connected": False}
    client = client_for(conn)
    checked = 0
    try:
        if not (conn.folders or {}).get("_root"):
            ensure_structure(client, conn)
        if not conn.changes_page_token:
            conn.changes_page_token = client.start_page_token()
        else:
            changes, new_token = client.changes(conn.changes_page_token)
            ids = [c["fileId"] for c in changes if c.get("fileId")]
            rows = (
                {
                    r.drive_file_id: r
                    for r in db.execute(select(DriveFile).where(DriveFile.drive_file_id.in_(ids))).scalars()
                }
                if ids
                else {}
            )
            for ch in changes:
                row = rows.get(ch.get("fileId"))
                if row is None:
                    continue
                checked += 1
                f = ch.get("file") or {}
                if ch.get("removed") or f.get("trashed"):
                    if row.sync_state != "remote_deleted":
                        row.sync_state = "remote_deleted"
                        row.last_error = "Eliminado en Drive"
                        _log(db, row, "reconcile", "conflict", "Eliminado o en la papelera de Drive")
                elif f.get("md5Checksum") and f.get("md5Checksum") != row.base_md5 and row.sync_state == "synced":
                    jobs.enqueue(
                        db, "drive.sync", {"row_id": str(row.id)}, dedupe_key=f"drive:{row.id}", max_attempts=8
                    )
            conn.changes_page_token = new_token
        due = (
            db.execute(
                select(DriveFile).where(
                    DriveFile.sync_state.in_(("pending", "error", "uploading")), DriveFile.next_attempt_at <= func.now()
                )
            )
            .scalars()
            .all()
        )
        for row in due:
            jobs.enqueue(db, "drive.sync", {"row_id": str(row.id)}, dedupe_key=f"drive:{row.id}", max_attempts=8)
        conn.status = "connected"
        conn.last_error = ""
        conn.last_checked_at = now_utc()
        conn.last_sync_at = now_utc()
    except DriveAuthError as exc:
        conn.status = "error"
        conn.last_error = str(exc)
        emit(db, "drive.changed", everyone=True)
        return {"connected": False, "error": str(exc)}
    except DriveError as exc:
        conn.last_error = str(exc)
        conn.last_checked_at = now_utc()
        return {"connected": True, "error": str(exc)}
    finally:
        client.close()
        stats = sync_counts(db)
        jobs.set_agent(
            db,
            "drive_sync",
            "Sincronizador de Drive",
            "error" if conn.status == "error" else ("working" if stats.get("uploading") else "idle"),
            "",
            stats={
                "connected": conn.status == "connected",
                "pending": stats.get("pending", 0) + stats.get("error", 0),
                "conflicts": stats.get("conflict", 0),
            },
        )
    emit(db, "sync.changed", everyone=True)
    return {"connected": True, "changes_checked": checked, "requeued": len(due)}


def sync_counts(db: Session) -> dict[str, int]:
    return dict(db.execute(select(DriveFile.sync_state, func.count()).group_by(DriveFile.sync_state)).all())


def area_slug(db: Session, area_id: uuid.UUID | None) -> str:
    if area_id is None:
        return "general"
    area = db.get(Area, area_id)
    return area.slug if area else "general"
