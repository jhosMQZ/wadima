"""Reuniones: configuración WebRTC para el navegador y participantes actuales de cada sala."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima.db import get_db
from wadima.meetings import MAX_PARTICIPANTS, ice_servers, meetings
from wadima.models import Area
from wadima.security.auth import Principal, require_password_ok
from wadima.security.perms import accessible_area_ids

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


@router.get("/config")
def config(principal: Principal = Depends(require_password_ok)):
    servers = ice_servers(principal.id)
    return {
        "ice_servers": servers,
        "turn_configured": any("turn:" in str(s.get("urls")) or "turns:" in str(s.get("urls")) for s in servers),
        "max_participants": MAX_PARTICIPANTS,
        "recording": False,
        "topology": "mesh",
    }


@router.get("/rooms")
def rooms(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    ids = accessible_area_ids(db, principal)
    areas = (
        db.execute(
            select(Area)
            .where(Area.id.in_(ids), Area.room_key == "meeting", Area.enabled.is_(True))
            .order_by(Area.template_key.is_(None), Area.name)
        )
        .scalars()
        .all()
        if ids
        else []
    )
    return [
        {
            "area_id": str(a.id),
            "name": a.name,
            "template_key": a.template_key,
            "participants": meetings.participants(a.id),
        }
        for a in areas
    ]
