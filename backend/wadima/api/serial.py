"""Serialización explícita de modelos a JSON (nunca se exponen hashes ni secretos)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from wadima.models import Area, User


def iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


def user_public(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id),
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "avatar": u.avatar or {},
        "is_active": u.is_active,
    }


def user_private(u: User) -> dict[str, Any]:
    return {
        **user_public(u),
        "email": u.email,
        "preferences": u.preferences or {},
        "must_change_password": u.must_change_password,
        "last_login_at": iso(u.last_login_at),
        "created_at": iso(u.created_at),
    }


def area_out(a: Area, role: str | None = None) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "slug": a.slug,
        "name": a.name,
        "description": a.description,
        "room_key": a.room_key,
        "color": a.color,
        "template_key": a.template_key,
        "icon": a.icon,
        "enabled": a.enabled,
        "settings": a.settings or {},
        "created_at": iso(a.created_at),
        "updated_at": iso(a.updated_at),
        "archived_at": iso(a.archived_at),
        "version": a.version,
        "my_role": role,
    }
