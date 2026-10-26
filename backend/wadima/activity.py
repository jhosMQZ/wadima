"""Registro de actividad de colaboración y notificaciones (a partir de acciones reales)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from wadima.models import ActivityEvent, Notification, User, UserStatus
from wadima.realtime import emit
from wadima.security.auth import now_utc


def touch_user_activity(db: Session, user_id: uuid.UUID, description: str) -> None:
    status = db.get(UserStatus, user_id)
    if status is None:
        status = UserStatus(user_id=user_id)
        db.add(status)
    status.last_activity_at = now_utc()
    status.last_activity = description[:200]


def log_activity(
    db: Session,
    kind: str,
    summary: str,
    *,
    user: User | None = None,
    actor_kind: str = "user",
    actor_name: str | None = None,
    area_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    ref_type: str | None = None,
    ref_id: str | uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
) -> ActivityEvent:
    ev = ActivityEvent(
        user_id=user.id if user else None,
        actor_kind=actor_kind if user is None else "user",
        actor_name=(user.display_name if user else actor_name or actor_kind)[:80],
        area_id=area_id,
        project_id=project_id,
        kind=kind,
        summary=summary[:400],
        ref_type=ref_type,
        ref_id=str(ref_id) if ref_id is not None else None,
        data=data or {},
    )
    db.add(ev)
    db.flush()
    if user is not None:
        touch_user_activity(db, user.id, summary)
    emit(
        db,
        "activity.new",
        area_id=area_id,
        id=ev.id,
        kind=kind,
        project_id=str(project_id) if project_id else None,
        user_id=str(user.id) if user else None,
    )
    return ev


def notify(db: Session, user_ids: set[uuid.UUID], kind: str, title: str, body: str = "", link: str = "") -> None:
    for uid in user_ids:
        n = Notification(user_id=uid, kind=kind, title=title[:200], body=body, link=link[:300])
        db.add(n)
    db.flush()
    if user_ids:
        emit(db, "notification.new", audience=set(user_ids), kind=kind, title=title[:200])
