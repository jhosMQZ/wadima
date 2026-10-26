"""Contenido que WADIMA sincroniza con Drive y cómo se aplican los cambios hechos directamente en Drive.

Nunca se exportan secretos: las copias excluyen hashes de contraseñas, sesiones, tokens OAuth y API keys.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from wadima import audit
from wadima.activity import log_activity
from wadima.drive.sync import area_slug, provider, remote_applier, track
from wadima.models import (
    ActivityEvent,
    Area,
    AreaMember,
    AreaVisit,
    Comment,
    Decision,
    Document,
    DocumentVersion,
    DriveFile,
    MemoryItem,
    MemoryRelation,
    Note,
    NoteRevision,
    Organization,
    Project,
    ProjectMember,
    Task,
    TimeEntry,
    TimeEvent,
    User,
)
from wadima.realtime import emit

# --------------------------------------------------------------------------- notas Markdown


def note_drive_name(note: Note) -> str:
    return f"{note.title}-{str(note.id)[:8]}.md"


def track_note(db: Session, note: Note, delay_seconds: int = 30) -> None:
    base = "Projects" if note.project_id else "Workspace"
    folder = f"{base}/{area_slug(db, note.area_id)}"
    track(
        db,
        "note",
        note.id,
        area_id=note.area_id,
        folder_key=folder,
        name=note_drive_name(note),
        mime="text/markdown",
        content=note.content.encode("utf-8"),
        delay_seconds=delay_seconds,
    )


@provider("note")
def note_content(db: Session, row: DriveFile) -> tuple[bytes, str]:
    note = db.get(Note, uuid.UUID(row.local_id))
    if note is None or note.deleted_at is not None:
        raise LookupError("la nota fue eliminada")
    return note.content.encode("utf-8"), "text/markdown"


@remote_applier("note")
def note_from_drive(db: Session, row: DriveFile, data: bytes, meta: dict) -> None:
    note = db.get(Note, uuid.UUID(row.local_id))
    if note is None:
        raise LookupError("la nota fue eliminada")
    text = data.decode("utf-8")
    note.version += 1
    note.content = text
    db.add(NoteRevision(note_id=note.id, version=note.version, title=note.title, content=text, author_id=None))
    log_activity(
        db,
        "note.updated",
        f"Importó desde Google Drive cambios en «{note.title}»",
        actor_kind="agent",
        actor_name="Sincronizador de Drive",
        area_id=note.area_id,
        project_id=note.project_id,
        ref_type="note",
        ref_id=note.id,
    )
    emit(db, "note.changed", area_id=note.area_id, id=str(note.id), version=note.version, by="drive")


# --------------------------------------------------------------------------- informes (resúmenes de proyecto)
def track_project_report(db: Session, project: Project) -> None:
    track(
        db,
        "report",
        project.id,
        area_id=project.area_id,
        folder_key=f"Reports/{area_slug(db, project.area_id)}",
        name=f"{project.slug}-resumen.md",
        mime="text/markdown",
        content=project.last_summary.encode("utf-8"),
    )


@provider("report")
def report_content(db: Session, row: DriveFile) -> tuple[bytes, str]:
    project = db.get(Project, uuid.UUID(row.local_id))
    if project is None:
        raise LookupError("el proyecto no existe")
    return project.last_summary.encode("utf-8"), "text/markdown"


# --------------------------------------------------------------------------- ancla de auditoría
def audit_anchor_payload(db: Session) -> bytes:
    result = audit.verify_chain(db)
    data = {
        "wadima": "audit-anchor",
        "chain_ok": result["ok"],
        "entries": result["checked"],
        "head": result.get("head"),
        "broken_at": result.get("broken_at"),
        "generated_at": datetime.now(UTC).isoformat(),
        "note": "Hash cabeza de la bitácora de auditoría. Drive conserva versiones anteriores de este archivo, "
        "lo que permite detectar reescrituras de la cadena en la base de datos.",
    }
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def track_audit_anchor(db: Session) -> None:
    track(
        db,
        "audit_anchor",
        "head",
        area_id=None,
        folder_key="Reports",
        name="audit-anchor.json",
        mime="application/json",
        content=audit_anchor_payload(db),
    )


@provider("audit_anchor")
def audit_anchor_content(db: Session, row: DriveFile) -> tuple[bytes, str]:
    return audit_anchor_payload(db), "application/json"


# --------------------------------------------------------------------------- copia lógica sin secretos
BACKUP_MODELS: list[tuple[type, set[str]]] = [
    # Identidad de la organización (el logotipo se exporta aparte, en base64) y sus políticas.
    (Organization, {"logo"}),
    (User, {"password_hash"}),  # se exporta la identidad, nunca el hash de la contraseña
    (Area, set()),
    (AreaMember, set()),
    (Project, set()),
    (ProjectMember, set()),
    (Task, set()),
    (Note, set()),
    (NoteRevision, set()),
    (Comment, set()),
    (Decision, set()),
    (Document, set()),
    (DocumentVersion, {"plain_text"}),
    (MemoryItem, {"tsv"}),
    (MemoryRelation, set()),
    (AreaVisit, set()),
    (ActivityEvent, set()),
    # Control horario: las sesiones de navegador no se copian, así que se omiten sus referencias.
    (TimeEntry, {"in_session_id", "out_session_id"}),
    (TimeEvent, set()),
]


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return None
    return value


def backup_payload(db: Session) -> bytes:
    out: dict[str, Any] = {"wadima_backup": 1, "generated_at": datetime.now(UTC).isoformat(), "tables": {}}
    for model, excluded in BACKUP_MODELS:
        cols = [c.key for c in inspect(model).columns if c.key not in excluded]
        rows = db.execute(select(model)).scalars().all()
        out["tables"][model.__tablename__] = [{c: _jsonable(getattr(r, c)) for c in cols} for r in rows]
        if model is Organization:
            for row, org in zip(out["tables"][model.__tablename__], rows, strict=True):
                row["logo_b64"] = base64.b64encode(org.logo).decode() if org.logo else None
    return json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def track_backup(db: Session, day: date | None = None) -> None:
    day = day or datetime.now(UTC).date()
    track(
        db,
        "backup",
        day.isoformat(),
        area_id=None,
        folder_key="Backups",
        name=f"wadima-backup-{day}.json",
        mime="application/json",
        content=backup_payload(db),
    )


@provider("backup")
def backup_content(db: Session, row: DriveFile) -> tuple[bytes, str]:
    return backup_payload(db), "application/json"
