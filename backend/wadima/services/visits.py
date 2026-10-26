"""Entradas y salidas de áreas con hora del servidor y auditoría encadenada."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima import audit
from wadima.models import Area, AreaVisit, AuthSession, User, UserStatus
from wadima.realtime import emit
from wadima.security.auth import now_utc


def open_visit(db: Session, session_id: uuid.UUID) -> AreaVisit | None:
    return db.execute(
        select(AreaVisit).where(AreaVisit.session_id == session_id, AreaVisit.left_at.is_(None))
    ).scalar_one_or_none()


def _status(db: Session, user_id: uuid.UUID) -> UserStatus:
    st = db.get(UserStatus, user_id)
    if st is None:
        st = UserStatus(user_id=user_id)
        db.add(st)
    return st


def close_visit(db: Session, visit: AreaVisit, reason: str, ip: str | None = None, extra: dict | None = None):
    entry = audit.record(
        db,
        "area.leave",
        actor_id=visit.user_id,
        target_type="area_visit",
        target_id=visit.id,
        area_id=visit.area_id,
        ip=ip,
        data={"reason": reason, **(extra or {})},
    )
    visit.left_at = entry.ts
    visit.end_reason = reason
    visit.exit_audit_id = entry.id
    db.flush()
    emit(db, "presence.changed", everyone=True, user_id=str(visit.user_id))
    return visit


def enter_area(db: Session, user: User, sess: AuthSession, area: Area, ip: str | None) -> AreaVisit:
    current = open_visit(db, sess.id)
    if current is not None:
        if current.area_id == area.id:
            return current
        close_visit(db, current, "switch", ip)
    visit_id = uuid.uuid4()
    entry = audit.record(
        db,
        "area.enter",
        actor_id=user.id,
        target_type="area_visit",
        target_id=visit_id,
        area_id=area.id,
        ip=ip,
        data={"session": str(sess.id)},
    )
    visit = AreaVisit(
        id=visit_id,
        user_id=user.id,
        area_id=area.id,
        session_id=sess.id,
        entered_at=entry.ts,
        entry_audit_id=entry.id,
    )
    db.add(visit)
    sess.active_area_id = area.id
    st = _status(db, user.id)
    st.area_id = area.id
    st.room_key = area.room_key
    db.flush()
    emit(db, "presence.changed", everyone=True, user_id=str(user.id))
    return visit


def leave_area(db: Session, sess: AuthSession, reason: str, ip: str | None = None) -> AreaVisit | None:
    visit = open_visit(db, sess.id)
    if visit is not None:
        close_visit(db, visit, reason, ip)
    sess.active_area_id = None
    st = db.get(UserStatus, sess.user_id)
    if st is not None:
        st.area_id = None
    return visit


def sweep_stale_visits(db: Session, online_users: set[uuid.UUID], inactivity_minutes: int) -> int:
    """Cierra entradas cuya sesión terminó o cuyo usuario lleva demasiado tiempo desconectado.
    La salida se registra con la hora de detección y se anota la última actividad conocida."""
    now = now_utc()
    closed = 0
    rows = db.execute(
        select(AreaVisit, AuthSession)
        .outerjoin(AuthSession, AuthSession.id == AreaVisit.session_id)
        .where(AreaVisit.left_at.is_(None))
    ).all()
    for visit, sess in rows:
        reason = None
        if sess is None or sess.revoked_at is not None or sess.expires_at <= now:
            reason = "session_ended"
        elif visit.user_id not in online_users:
            st = db.get(UserStatus, visit.user_id)
            last_seen = (st.last_seen_at if st and st.last_seen_at else sess.last_seen_at) or visit.entered_at
            if now - last_seen > timedelta(minutes=inactivity_minutes):
                reason = "inactivity"
        if reason:
            last = sess.last_seen_at.isoformat() if sess is not None else None
            close_visit(db, visit, reason, extra={"session_last_seen": last})
            if sess is not None and sess.active_area_id == visit.area_id:
                sess.active_area_id = None
            closed += 1
    return closed
