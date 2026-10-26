"""Recuperación y reconstrucción.

1. Copia completa local: `python manage.py db backup` (pg_dump) + carpeta var/blobs. Restaurar con pg_restore.
2. Desastre local (equipo perdido): la copia lógica diaria de Drive (Backups/wadima-backup-AAAA-MM-DD.json, sin
   secretos) reconstruye la estructura, notas, metadatos y memoria; los originales se recuperan desde
   Drive (Library/) buscando su referencia estable `wadimaRef` y verificando su SHA-256.
Las contraseñas no viajan en la copia: tras restaurar, cada usuario recibe una contraseña temporal.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Integer, func, inspect, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Session

from wadima import audit
from wadima.drive.providers import BACKUP_MODELS
from wadima.library.storage import blob_path, store_blob
from wadima.models import Area, DocumentVersion, DriveFile, Organization, Task, User
from wadima.security.crypto import hash_password, random_token


class RestoreError(Exception):
    pass


def _convert(model, row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    columns = {c.key: c for c in inspect(model).columns}
    for key, value in row.items():
        col = columns.get(key)
        if col is None or col.computed is not None:
            continue
        if value is not None:
            if isinstance(col.type, DateTime):
                value = datetime.fromisoformat(value)
            elif isinstance(col.type, Date):
                value = date.fromisoformat(value)
            elif isinstance(col.type, PGUUID):
                value = uuid.UUID(value)
        out[key] = value
    return out


def restore_logical(db: Session, payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    if payload.get("wadima_backup") != 1:
        raise RestoreError("El archivo no es una copia lógica de WADIMA")
    if not force and (
        db.execute(select(func.count()).select_from(User)).scalar_one()
        or db.execute(select(func.count()).select_from(Area)).scalar_one()
    ):
        raise RestoreError("La base de datos no está vacía; restaure sobre una base nueva (o use --force)")
    tables = payload["tables"]
    counts: dict[str, int] = {}
    temp_passwords: dict[str, str] = {}
    for model, _excluded in BACKUP_MODELS:
        rows = tables.get(model.__tablename__, [])
        if model is Task:
            rows = _parents_first(rows)
        # Las claves enteras autoincrementales (actividad, eventos horarios) las vuelve a asignar la base de datos
        # para que sus secuencias sigan siendo coherentes; se conserva el orden original.
        int_pk = isinstance(model.__table__.c.id.type, Integer | BigInteger)
        for raw in rows:
            data = _convert(model, raw)
            if int_pk:
                data.pop("id", None)
            if model is Organization:
                # La migración ya creó la organización por defecto: se actualiza en lugar de duplicarla.
                logo = base64.b64decode(raw["logo_b64"]) if raw.get("logo_b64") else None
                org = db.get(Organization, data["id"])
                if org is None:
                    db.add(Organization(**data, logo=logo))
                else:
                    for key, value in data.items():
                        setattr(org, key, value)
                    org.logo = logo
                continue
            if model is User:
                temp = random_token(12)
                temp_passwords[data["username"]] = temp
                data["password_hash"] = hash_password(temp)
                data["must_change_password"] = True
            db.add(model(**data))
        db.flush()
        counts[model.__tablename__] = len(rows)
    audit.record(
        db,
        "system.restored",
        data={"source": "copia lógica", "generated_at": payload.get("generated_at"), "counts": counts},
    )
    return {"counts": counts, "temporary_passwords": temp_passwords}


def _parents_first(rows: list[dict]) -> list[dict]:
    by_id = {r["id"]: r for r in rows}
    ordered, seen = [], set()

    def visit(r):
        if r["id"] in seen:
            return
        parent = r.get("parent_id")
        if parent and parent in by_id:
            visit(by_id[parent])
        seen.add(r["id"])
        ordered.append(r)

    for r in rows:
        visit(r)
    return ordered


def missing_blobs(db: Session) -> list[DocumentVersion]:
    return [v for v in db.execute(select(DocumentVersion)).scalars() if not blob_path(v.blob_path).exists()]


def rebuild_blobs_from_drive(db: Session) -> dict[str, Any]:
    """Descarga desde Drive los originales que faltan en el almacén local, verificando su SHA-256."""
    from wadima.drive.sync import client_for, workspace_connection

    conn = workspace_connection(db)
    if conn is None:
        raise RestoreError("Conecte Google Drive para recuperar los originales")
    client = client_for(conn)
    restored, failed = 0, []
    try:
        for v in missing_blobs(db):
            ref = f"document_original:{v.id}"
            row = db.execute(
                select(DriveFile).where(DriveFile.local_kind == "document_original", DriveFile.local_id == str(v.id))
            ).scalar_one_or_none()
            file_id = row.drive_file_id if row and row.drive_file_id else None
            if file_id is None:
                found = client.find_by_property("wadimaRef", ref)
                file_id = found[0]["id"] if found else None
            if file_id is None:
                failed.append({"version_id": str(v.id), "error": "no está en Drive"})
                continue
            data = client.download(file_id)
            if hashlib.sha256(data).hexdigest() != v.sha256:
                failed.append({"version_id": str(v.id), "error": "el SHA-256 no coincide"})
                continue
            store_blob(data)
            restored += 1
    finally:
        client.close()
    return {"restored": restored, "failed": failed}


def load_backup_file(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
