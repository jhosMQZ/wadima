"""Identidad de la organización, zona horaria, plantillas de áreas y configuración inicial."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima.models import DEFAULT_ORG_ID, Area, AreaMember, Organization, User

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
LOGO_MAX_BYTES = 512 * 1024
# Solo mapas de bits: un SVG puede contener scripts.
LOGO_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}

DEFAULT_TIME_SETTINGS: dict[str, Any] = {
    # none: una jornada sin salida queda «pendiente de cierre» (nunca se inventa la hora).
    # last_activity: se cierra automáticamente a la hora de la última conexión registrada, marcada como automática.
    "auto_close": "none",
    "max_open_hours": 14,
    "disconnect_minutes": 30,
    # all: los socios ven el historial de todos; admins: solo aprobadores y el propio socio.
    "visibility": "all",
}

# Plantillas de áreas predefinidas. room_key es la sala de la oficina y module la ruta del frontend.
AREA_TEMPLATES: dict[str, dict[str, Any]] = {
    "ops": {
        "name": "Centro de operaciones",
        "description": "Panel principal: presencia de los socios, actividad reciente y estado general.",
        "icon": "◉",
        "room_key": "ops",
        "module": "/ops",
        "color": "#3f7cd8",
        "member_role": "editor",
        "required": True,
    },
    "dev": {
        "name": "Desarrollo",
        "description": "Project Studio: código, Git, vista previa en vivo y proyectos técnicos.",
        "icon": "⌘",
        "room_key": "dev",
        "module": "/studio",
        "color": "#8a6a4a",
        "member_role": "editor",
    },
    "library": {
        "name": "Biblioteca",
        "description": "Documentos originales, conversión a Markdown, búsqueda y memoria compartida.",
        "icon": "▥",
        "room_key": "library",
        "module": "/library",
        "color": "#b07a3c",
        "member_role": "editor",
    },
    "ai": {
        "name": "Laboratorio de IA",
        "description": "Conexión de cada socio con Claude, tareas para agentes y Memory Agent.",
        "icon": "✦",
        "room_key": "ai",
        "module": "/agents",
        "color": "#8b5cf6",
        "member_role": "editor",
    },
    "meeting": {
        "name": "Reuniones",
        "description": "Sala de audio en tiempo real, actas y decisiones.",
        "icon": "☰",
        "room_key": "meeting",
        "module": "/meetings",
        "color": "#c9a86a",
        "member_role": "editor",
    },
    "admin": {
        "name": "Administración",
        "description": "Control horario, aprobaciones, usuarios y auditoría. Los propietarios aprueban jornadas.",
        "icon": "⛭",
        "room_key": "admin",
        "module": "/time",
        "color": "#3fa66b",
        "member_role": "viewer",
    },
    "servers": {
        "name": "Servidores e integraciones",
        "description": "Google Drive, conexiones, trabajos en segundo plano y estado del sistema.",
        "icon": "⇄",
        "room_key": "servers",
        "module": "/connections",
        "color": "#4a5568",
        "member_role": "viewer",
    },
}

ROOM_MODULES = {t["room_key"]: t["module"] for t in AREA_TEMPLATES.values()}


class OrgError(ValueError):
    pass


def get_org(db: Session, org_id: uuid.UUID = DEFAULT_ORG_ID) -> Organization:
    org = db.get(Organization, org_id)
    if org is None:  # instalación creada sin la migración de datos (no debería ocurrir)
        org = Organization(id=org_id, slug="default", settings={})
        db.add(org)
        db.flush()
    return org


def time_settings(org: Organization) -> dict[str, Any]:
    return {**DEFAULT_TIME_SETTINGS, **((org.settings or {}).get("time") or {})}


def zone(org: Organization) -> ZoneInfo:
    try:
        return ZoneInfo(org.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise OrgError(f"Zona horaria desconocida: {name}") from exc
    return name


def logo_url(org: Organization) -> str | None:
    return f"/api/org/logo?v={org.logo_sha256[:12]}" if org.logo and org.logo_sha256 else None


def org_public(org: Organization) -> dict[str, Any]:
    """Lo que necesita la pantalla de inicio de sesión (sin autenticar): solo la marca."""
    return {
        "platform_name": org.platform_name,
        "legal_name": org.legal_name,
        "short_mark": org.short_mark,
        "primary_color": org.primary_color,
        "secondary_color": org.secondary_color,
        "logo_url": logo_url(org),
        "timezone": org.timezone,
    }


def org_full(org: Organization) -> dict[str, Any]:
    return {
        **org_public(org),
        "id": str(org.id),
        "description": org.description,
        "time": time_settings(org),
        "setup_completed": org.setup_completed_at is not None,
        "setup_completed_at": org.setup_completed_at.isoformat() if org.setup_completed_at else None,
        "version": org.version,
    }


def clean_logo(data: bytes, declared_mime: str | None) -> tuple[bytes, str]:
    if not data:
        raise OrgError("El archivo está vacío")
    if len(data) > LOGO_MAX_BYTES:
        raise OrgError("El logotipo no puede superar 512 KB")
    for mime, sigs in LOGO_SIGNATURES.items():
        if any(data.startswith(s) for s in sigs) and (mime != "image/webp" or data[8:12] == b"WEBP"):
            return data, mime
    raise OrgError("Formato no admitido: use PNG, JPEG o WebP (SVG no se admite por seguridad)")


def set_logo(org: Organization, data: bytes | None, mime: str | None) -> None:
    if data is None:
        org.logo = org.logo_mime = org.logo_sha256 = None
        return
    data, mime = clean_logo(data, mime)
    org.logo = data
    org.logo_mime = mime
    org.logo_sha256 = hashlib.sha256(data).hexdigest()


def contrast_ratio(a: str, b: str) -> float:
    def lum(h: str) -> float:
        rgb = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# --------------------------------------------------------------------------- áreas predefinidas
def template_area(db: Session, key: str, org_id: uuid.UUID = DEFAULT_ORG_ID) -> Area | None:
    return db.execute(
        select(Area).where(Area.organization_id == org_id, Area.template_key == key, Area.archived_at.is_(None))
    ).scalar_one_or_none()


def ensure_template_area(db: Session, key: str, actor: User | None, org_id: uuid.UUID = DEFAULT_ORG_ID) -> Area:
    """Devuelve el área de la plantilla, creándola si no existe. El centro de operaciones adopta el área general
    existente (sala de operaciones, sin plantilla) en lugar de duplicarla."""
    tpl = AREA_TEMPLATES[key]
    area = template_area(db, key, org_id)
    if area is None:
        if key == "ops":  # el área general existente pasa a ser el centro de operaciones
            area = db.execute(
                select(Area)
                .where(
                    Area.organization_id == org_id,
                    Area.template_key.is_(None),
                    Area.room_key == "ops",
                    Area.archived_at.is_(None),
                )
                .order_by(Area.created_at)
                .limit(1)
            ).scalar_one_or_none()
        if area is None:
            from wadima.api.areas import slugify, unique_slug

            area = Area(
                organization_id=org_id,
                slug=unique_slug(db, slugify(tpl["name"])),
                name=tpl["name"],
                description=tpl["description"],
                room_key=tpl["room_key"],
                color=tpl["color"],
                created_by=actor.id if actor else None,
            )
            db.add(area)
        area.template_key = key
        area.icon = tpl["icon"]
        db.flush()
    return area


def add_default_members(db: Session, area: Area, key: str, users: list[User]) -> int:
    role = AREA_TEMPLATES[key]["member_role"]
    existing = set(db.execute(select(AreaMember.user_id).where(AreaMember.area_id == area.id)).scalars())
    added = 0
    for u in users:
        if u.id in existing or not u.is_active:
            continue
        db.add(AreaMember(area_id=area.id, user_id=u.id, role="owner" if u.role == "admin" else role))
        added += 1
    db.flush()
    return added


def templates_out(db: Session, org_id: uuid.UUID = DEFAULT_ORG_ID) -> list[dict[str, Any]]:
    out = []
    for key, tpl in AREA_TEMPLATES.items():
        area = template_area(db, key, org_id)
        out.append(
            {
                "key": key,
                "name": tpl["name"],
                "description": tpl["description"],
                "icon": tpl["icon"],
                "room_key": tpl["room_key"],
                "module": tpl["module"],
                "member_role": tpl["member_role"],
                "required": bool(tpl.get("required")),
                "area_id": str(area.id) if area else None,
                "area_name": area.name if area else None,
                "enabled": bool(area and area.enabled),
            }
        )
    return out
