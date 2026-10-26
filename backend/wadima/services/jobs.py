"""Cola de trabajos persistente en PostgreSQL con reintentos exponenciales y recuperación tras caídas."""

from __future__ import annotations

import logging
import os
import socket
import traceback
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from wadima.db import SessionLocal
from wadima.models import AgentState, Job
from wadima.realtime import emit
from wadima.security.auth import now_utc

log = logging.getLogger("wadima.jobs")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
Handler = Callable[[Session, dict[str, Any]], dict[str, Any] | None]
HANDLERS: dict[str, tuple[str, Handler]] = {}  # kind -> (agent_key, handler)


class RetryLater(Exception):  # noqa: N818 - semántica de control, no de error
    """El trabajo no puede completarse ahora (p. ej. Drive desconectado); se reintentará sin consumir intentos."""

    def __init__(self, message: str, seconds: int = 120):
        super().__init__(message)
        self.seconds = seconds


def register(kind: str, agent_key: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = (agent_key, fn)
        return fn

    return deco


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    delay_seconds: int = 0,
    max_attempts: int = 5,
) -> Job:
    """Añade un trabajo en la transacción actual. Con dedupe_key, un trabajo pendiente igual se reutiliza
    (y se pospone), lo que agrupa ráfagas de cambios (p. ej. autosave) en un solo procesamiento."""
    run_after = now_utc() + timedelta(seconds=delay_seconds)
    if dedupe_key:
        existing = db.execute(
            select(Job).where(Job.dedupe_key == dedupe_key, Job.status == "queued").with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if existing is not None:
            existing.run_after = max(existing.run_after, run_after)
            existing.payload = payload
            return existing
    job = Job(kind=kind, payload=payload, dedupe_key=dedupe_key, run_after=run_after, max_attempts=max_attempts)
    db.add(job)
    db.flush()
    return job


def claim(db: Session, kinds: list[str]) -> Job | None:
    row = db.execute(
        select(Job)
        .where(Job.status == "queued", Job.run_after <= text("now()"), Job.kind.in_(kinds))
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if row is None:
        return None
    row.status = "running"
    row.locked_by = WORKER_ID
    row.locked_at = now_utc()
    row.attempts += 1
    return row


def recover_stale(db: Session, minutes: int = 15) -> int:
    """Devuelve a la cola los trabajos que quedaron 'running' por una caída del proceso."""
    cutoff = now_utc() - timedelta(minutes=minutes)
    rows = db.execute(select(Job).where(Job.status == "running", Job.locked_at < cutoff)).scalars().all()
    for job in rows:
        job.status = "queued"
        job.last_error = (job.last_error + "\nRecuperado tras interrupción del worker").strip()
        job.locked_by = None
    return len(rows)


def set_agent(
    db: Session,
    key: str,
    name: str,
    status: str,
    current: str = "",
    last_result: str | None = None,
    stats: dict | None = None,
) -> None:
    st = db.get(AgentState, key)
    changed = st is None or st.status != status or st.current != current
    if st is None:
        st = AgentState(key=key, name=name)
        db.add(st)
    st.status = status
    st.current = current[:300]
    if last_result is not None:
        st.last_result = last_result[:2000]
    if stats is not None:
        st.stats = {**(st.stats or {}), **stats}
    st.heartbeat_at = now_utc()
    if changed:
        emit(db, "agent.changed", everyone=True, key=key, status=status)


AGENT_NAMES = {
    "doc_pipeline": "Conversor documental",
    "memory_agent": "Memory Agent",
    "drive_sync": "Sincronizador de Drive",
}


def process_one(kinds: list[str] | None = None) -> bool:
    """Procesa un trabajo si hay alguno disponible. Devuelve True si procesó algo."""
    kinds = kinds or list(HANDLERS)
    if not kinds:
        return False
    db = SessionLocal()
    try:
        job = claim(db, kinds)
        if job is None:
            db.commit()
            return False
        agent_key, handler = HANDLERS[job.kind]
        set_agent(db, agent_key, AGENT_NAMES.get(agent_key, agent_key), "working", f"{job.kind} #{job.id}")
        db.commit()
        job_id, payload, attempts, max_attempts = job.id, dict(job.payload), job.attempts, job.max_attempts
    finally:
        db.close()

    work = SessionLocal()
    try:
        result = handler(work, payload) or {}
        work.commit()
        outcome, error, retry_in = "done", "", 0
    except RetryLater as exc:
        work.rollback()
        outcome, error, retry_in = "retry_later", str(exc), exc.seconds
        result = {}
    except Exception as exc:  # noqa: BLE001
        work.rollback()
        log.exception("Fallo en trabajo %s #%s", payload, job_id)
        outcome, error, retry_in = "error", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}", 0
        result = {}
    finally:
        work.close()

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if outcome == "done":
            job.status = "done"
            job.result = result
            job.finished_at = now_utc()
            last = f"{job.kind} #{job.id} completado"
        elif outcome == "retry_later":
            job.status = "queued"
            job.attempts = max(0, attempts - 1)
            job.run_after = now_utc() + timedelta(seconds=retry_in)
            job.last_error = error[-4000:]
            last = f"{job.kind} #{job.id} en espera: {error[:200]}"
        else:
            job.last_error = error[-4000:]
            if attempts >= max_attempts:
                job.status = "dead"
                job.finished_at = now_utc()
            else:
                job.status = "queued"
                job.run_after = now_utc() + timedelta(seconds=min(3600, 10 * 2**attempts))
            last = f"{job.kind} #{job.id} falló: {error.splitlines()[0][:200]}"
        job.locked_by = None
        agent_kinds = [k for k, (a, _) in HANDLERS.items() if a == agent_key]
        pending = db.execute(
            select(Job.id)
            .where(
                Job.kind.in_(agent_kinds),
                or_(Job.status == "running", and_(Job.status == "queued", Job.run_after <= text("now()"))),
            )
            .limit(1)
        ).first()
        set_agent(
            db,
            agent_key,
            AGENT_NAMES.get(agent_key, agent_key),
            "error" if outcome == "error" and job.status == "dead" else ("working" if pending else "idle"),
            "",
            last_result=last,
        )
        db.commit()
    finally:
        db.close()
    return True


def drain(kinds: list[str] | None = None, max_jobs: int = 100) -> int:
    """Procesa trabajos hasta vaciar la cola disponible (usado por tests y por el worker periódico)."""
    n = 0
    while n < max_jobs and process_one(kinds):
        n += 1
    return n
