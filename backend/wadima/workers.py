"""Workers embebidos: cola de trabajos (pipeline documental, Memory Agent, Drive) y tareas periódicas."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from wadima.config import get_settings
from wadima.db import session_scope
from wadima.models import AgentState, DriveFile
from wadima.services import jobs

log = logging.getLogger("wadima.workers")


def load_handlers() -> None:
    """Importa los módulos que registran manejadores de trabajos y proveedores de contenido."""
    import wadima.drive.providers  # noqa: F401
    import wadima.drive.sync  # noqa: F401
    import wadima.library.pipeline  # noqa: F401
    import wadima.memory.agent  # noqa: F401


def _process() -> None:
    jobs.drain(max_jobs=10)


def _heartbeat() -> None:
    agent_keys = {agent for agent, _ in jobs.HANDLERS.values()}
    with session_scope() as db:
        jobs.recover_stale(db)
        for key in agent_keys:
            st = db.get(AgentState, key)
            status = st.status if st and st.status in ("working", "error") else "idle"
            jobs.set_agent(db, key, jobs.AGENT_NAMES.get(key, key), status, st.current if st else "")


def _drive_reconcile() -> None:
    from wadima.drive.sync import reconcile

    with session_scope() as db:
        reconcile(db)


def _drive_snapshots() -> None:
    """Ancla de auditoría (cada hora) y copia lógica diaria, solo con Drive conectado."""
    from wadima.drive.providers import track_audit_anchor, track_backup
    from wadima.drive.sync import workspace_connection

    with session_scope() as db:
        if workspace_connection(db) is None:
            return
        anchor = db.execute(select(DriveFile).where(DriveFile.local_kind == "audit_anchor")).scalar_one_or_none()
        if anchor is None or anchor.updated_at < datetime.now(UTC) - timedelta(hours=1):
            track_audit_anchor(db)
        today = datetime.now(UTC).date().isoformat()
        exists = db.execute(
            select(DriveFile.id).where(DriveFile.local_kind == "backup", DriveFile.local_id == today)
        ).first()
        if not exists:
            track_backup(db)


def register_workers(register: Callable[[str, float, Callable[[], None]], None]) -> None:
    load_handlers()
    register("jobs", 2, _process)
    register("agent-heartbeat", 60, _heartbeat)
    register("drive-reconcile", get_settings().drive_poll_seconds, _drive_reconcile)
    register("drive-snapshots", 600, _drive_snapshots)
