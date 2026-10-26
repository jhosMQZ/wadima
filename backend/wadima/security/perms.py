"""Autorización por rol global y rol en el área. Los administradores tienen rol 'owner' implícito."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima.models import Area, AreaMember
from wadima.security.auth import Principal

RANK = {"viewer": 1, "editor": 2, "owner": 3}


def area_role(db: Session, principal: Principal, area_id: uuid.UUID) -> str | None:
    if principal.is_admin:
        return "owner"
    return db.execute(
        select(AreaMember.role).where(AreaMember.area_id == area_id, AreaMember.user_id == principal.id)
    ).scalar_one_or_none()


def require_area(db: Session, principal: Principal, area_id: uuid.UUID, min_role: str = "viewer") -> Area:
    area = db.get(Area, area_id)
    if area is None or area.archived_at is not None and not principal.is_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Área no encontrada")
    role = area_role(db, principal, area_id)
    if role is None:
        # 404 para no revelar la existencia de áreas ajenas.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Área no encontrada")
    if RANK[role] < RANK[min_role]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requiere rol '{min_role}' en el área")
    return area


def accessible_area_ids(db: Session, principal: Principal) -> list[uuid.UUID]:
    if principal.is_admin:
        return list(db.execute(select(Area.id).where(Area.archived_at.is_(None))).scalars())
    return list(
        db.execute(
            select(AreaMember.area_id)
            .join(Area, Area.id == AreaMember.area_id)
            .where(AreaMember.user_id == principal.id, Area.archived_at.is_(None))
        ).scalars()
    )
