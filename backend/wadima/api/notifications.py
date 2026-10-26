from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from wadima.api.serial import iso
from wadima.db import get_db
from wadima.models import Notification
from wadima.security.auth import Principal, now_utc, require_password_ok

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Notification)
        .where(Notification.user_id == principal.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    ).scalars()
    unread = db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == principal.id, Notification.read_at.is_(None))
    ).scalar_one()
    return {
        "unread": unread,
        "items": [
            {
                "id": str(n.id),
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "created_at": iso(n.created_at),
                "read_at": iso(n.read_at),
            }
            for n in rows
        ],
    }


@router.post("/read")
def mark_read(
    ids: list[uuid.UUID] | None = None,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    q = update(Notification).where(Notification.user_id == principal.id, Notification.read_at.is_(None))
    if ids:
        q = q.where(Notification.id.in_(ids))
    db.execute(q.values(read_at=now_utc()))
    db.commit()
    return {"ok": True}
