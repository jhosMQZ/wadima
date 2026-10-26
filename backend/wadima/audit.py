"""Bitácora de auditoría encadenada por hash.

Cada entrada guarda hash = SHA-256(prev_hash + JSON canónico de la entrada). La hora es la del
servidor de base de datos (clock_timestamp) y un trigger impide modificar o borrar filas, de modo
que cualquier alteración directa rompe la cadena y es detectable con verify_chain().
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from wadima.models import AuditLog
from wadima.security.crypto import sha256_hex

GENESIS = "0" * 64
_LOCK_KEY = 0x5741_4449  # "WADI"


def _canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _entry_payload(row: AuditLog | dict[str, Any]) -> dict[str, Any]:
    get = row.get if isinstance(row, dict) else lambda k: getattr(row, k)
    ts: datetime = get("ts")
    return {
        "ts": ts.astimezone(UTC).isoformat(timespec="microseconds"),
        "actor": str(get("actor_id")) if get("actor_id") else None,
        "action": get("action"),
        "target_type": get("target_type"),
        "target_id": get("target_id"),
        "area": str(get("area_id")) if get("area_id") else None,
        "ip": get("ip"),
        "data": get("data") or {},
    }


def compute_hash(prev_hash: str, row: AuditLog | dict[str, Any]) -> str:
    return sha256_hex(prev_hash + _canonical(_entry_payload(row)))


def record(
    db: Session,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    area_id: uuid.UUID | None = None,
    ip: str | None = None,
    data: dict[str, Any] | None = None,
) -> AuditLog:
    """Inserta una entrada en la transacción actual (el llamador hace commit)."""
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    prev = db.execute(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)).scalar() or GENESIS
    ts = db.execute(text("SELECT clock_timestamp()")).scalar_one()
    # Normalizar datos a JSON puro para que el hash sea reproducible tras pasar por JSONB.
    clean = json.loads(_canonical(data or {}))
    fields = {
        "ts": ts,
        "actor_id": actor_id,
        "action": action,
        "target_type": target_type,
        "target_id": str(target_id) if target_id is not None else None,
        "area_id": area_id,
        "ip": ip,
        "data": clean,
    }
    entry = AuditLog(**fields, prev_hash=prev, hash=compute_hash(prev, fields))
    db.add(entry)
    db.flush()
    return entry


def verify_chain(db: Session, batch: int = 2000) -> dict[str, Any]:
    prev = GENESIS
    checked = 0
    last_id = 0
    while True:
        rows = (
            db.execute(select(AuditLog).where(AuditLog.id > last_id).order_by(AuditLog.id).limit(batch)).scalars().all()
        )
        if not rows:
            break
        for row in rows:
            if row.prev_hash != prev or compute_hash(prev, row) != row.hash:
                return {"ok": False, "checked": checked, "broken_at": row.id}
            prev = row.hash
            checked += 1
            last_id = row.id
    return {"ok": True, "checked": checked, "head": prev}
