"""Identidad de la organización, plantillas de áreas y asistente de configuración inicial."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima import audit
from wadima import org as orgsvc
from wadima.api.serial import user_public
from wadima.db import get_db
from wadima.models import Area, User
from wadima.realtime import emit
from wadima.security.auth import Principal, client_ip, now_utc, require_admin, require_password_ok

router = APIRouter(prefix="/api/org", tags=["organization"])

COLOR = r"^#[0-9a-fA-F]{6}$"


class TimePolicyIn(BaseModel):
    auto_close: str = Field(default="none", pattern="^(none|last_activity)$")
    max_open_hours: int = Field(default=14, ge=1, le=48)
    disconnect_minutes: int = Field(default=30, ge=5, le=720)
    visibility: str = Field(default="all", pattern="^(all|admins)$")


class OrgIn(BaseModel):
    version: int
    platform_name: str = Field(min_length=2, max_length=60)
    legal_name: str = Field(min_length=2, max_length=160)
    short_mark: str = Field(min_length=1, max_length=4)
    description: str = Field(default="", max_length=2000)
    primary_color: str = Field(pattern=COLOR)
    secondary_color: str = Field(pattern=COLOR)
    timezone: str = Field(min_length=1, max_length=64)
    time: TimePolicyIn | None = None


def _changed(org, body: OrgIn) -> dict:
    fields = ("platform_name", "legal_name", "short_mark", "description", "primary_color", "secondary_color")
    return {f: getattr(body, f) for f in fields if getattr(org, f) != getattr(body, f)}


def _apply(org, body: OrgIn) -> dict:
    changes = _changed(org, body)
    try:
        tz = orgsvc.validate_timezone(body.timezone.strip())
    except orgsvc.OrgError as exc:
        raise HTTPException(422, str(exc)) from exc
    if tz != org.timezone:
        changes["timezone"] = tz
    for f, v in changes.items():
        setattr(org, f, v.strip() if isinstance(v, str) else v)
    if body.time is not None:
        current = orgsvc.time_settings(org)
        new = body.time.model_dump()
        if new != {k: current.get(k) for k in new}:
            changes["time"] = new
            org.settings = {**(org.settings or {}), "time": new}
    return changes


@router.get("/public")
def public_identity(db: Session = Depends(get_db)):
    """Marca visible antes de iniciar sesión (nombre, iniciales, colores y logotipo)."""
    return orgsvc.org_public(orgsvc.get_org(db))


@router.get("/logo")
def logo(db: Session = Depends(get_db)):
    org = orgsvc.get_org(db)
    if not org.logo:
        raise HTTPException(404, "Sin logotipo")
    return Response(
        org.logo,
        media_type=org.logo_mime or "application/octet-stream",
        headers={
            "Cache-Control": "public, max-age=86400",
            "Content-Security-Policy": "default-src 'none'",
            "ETag": f'"{org.logo_sha256}"',
        },
    )


@router.get("")
def get_identity(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    org = orgsvc.get_org(db)
    return {**orgsvc.org_full(org), "templates": orgsvc.templates_out(db, org.id)}


@router.put("")
def update_identity(
    body: OrgIn, request: Request, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)
):
    org = orgsvc.get_org(db)
    if body.version != org.version:
        raise HTTPException(409, "Otra persona cambió la configuración; recargue antes de guardar")
    changes = _apply(org, body)
    if changes:
        org.version += 1
        audit.record(
            db,
            "org.updated",
            actor_id=principal.id,
            target_type="organization",
            target_id=org.id,
            ip=client_ip(request),
            data={"changes": changes},
        )
        emit(db, "org.updated", everyone=True)
    db.commit()
    return orgsvc.org_full(org)


@router.put("/logo")
async def upload_logo(
    request: Request,
    file: UploadFile = File(...),
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    data = await file.read(orgsvc.LOGO_MAX_BYTES + 1)
    org = orgsvc.get_org(db)
    try:
        orgsvc.set_logo(org, data, file.content_type)
    except orgsvc.OrgError as exc:
        raise HTTPException(422, str(exc)) from exc
    org.version += 1
    audit.record(
        db,
        "org.logo_set",
        actor_id=principal.id,
        target_type="organization",
        target_id=org.id,
        ip=client_ip(request),
        data={"sha256": org.logo_sha256, "bytes": len(data), "mime": org.logo_mime},
    )
    emit(db, "org.updated", everyone=True)
    db.commit()
    return orgsvc.org_full(org)


@router.delete("/logo")
def delete_logo(request: Request, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    org = orgsvc.get_org(db)
    orgsvc.set_logo(org, None, None)
    org.version += 1
    audit.record(db, "org.logo_removed", actor_id=principal.id, target_type="organization", ip=client_ip(request))
    emit(db, "org.updated", everyone=True)
    db.commit()
    return orgsvc.org_full(org)


# --------------------------------------------------------------------------- plantillas de áreas
@router.get("/templates")
def templates(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    return orgsvc.templates_out(db)


def _active_users(db: Session) -> list[User]:
    return list(db.execute(select(User).where(User.is_active)).scalars())


def _set_template_enabled(db: Session, principal: Principal, key: str, enabled: bool, ip: str | None) -> Area:
    if key not in orgsvc.AREA_TEMPLATES:
        raise HTTPException(404, "Plantilla desconocida")
    if not enabled and orgsvc.AREA_TEMPLATES[key].get("required"):
        raise HTTPException(422, "El centro de operaciones es el punto de entrada y no se puede deshabilitar")
    area = orgsvc.template_area(db, key)
    if area is None:
        if not enabled:
            raise HTTPException(404, "Esa área no existe")
        area = orgsvc.ensure_template_area(db, key, principal.user)
        orgsvc.add_default_members(db, area, key, _active_users(db))
    if area.enabled != enabled:
        area.enabled = enabled
        area.version += 1
        audit.record(
            db,
            "area.enabled" if enabled else "area.disabled",
            actor_id=principal.id,
            target_type="area",
            target_id=area.id,
            area_id=area.id,
            ip=ip,
            data={"template": key},
        )
    emit(db, "area.updated", everyone=True, id=str(area.id))
    return area


@router.post("/templates/{key}/enable")
def enable_template(
    key: str, request: Request, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)
):
    _set_template_enabled(db, principal, key, True, client_ip(request))
    db.commit()
    return orgsvc.templates_out(db)


@router.post("/templates/{key}/disable")
def disable_template(
    key: str, request: Request, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)
):
    _set_template_enabled(db, principal, key, False, client_ip(request))
    db.commit()
    return orgsvc.templates_out(db)


# --------------------------------------------------------------------------- configuración inicial
class SetupUserIn(BaseModel):
    id: uuid.UUID
    display_name: str = Field(min_length=2, max_length=80)
    email: str | None = Field(default=None, max_length=200)


class SetupIn(OrgIn):
    areas: list[str] = Field(default_factory=list)
    users: list[SetupUserIn] = Field(default_factory=list)


@router.get("/setup")
def setup_state(principal: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    org = orgsvc.get_org(db)
    users = db.execute(select(User).where(User.is_active).order_by(User.created_at, User.username)).scalars()
    return {
        "org": orgsvc.org_full(org),
        "templates": orgsvc.templates_out(db, org.id),
        "users": [{**user_public(u), "email": u.email} for u in users],
    }


@router.post("/setup")
def complete_setup(
    body: SetupIn, request: Request, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)
):
    org = orgsvc.get_org(db)
    if body.version != org.version:
        raise HTTPException(409, "Otra persona cambió la configuración; recargue antes de guardar")
    unknown = [k for k in body.areas if k not in orgsvc.AREA_TEMPLATES]
    if unknown:
        raise HTTPException(422, f"Plantillas desconocidas: {', '.join(unknown)}")
    ip = client_ip(request)
    changes = _apply(org, body)
    for item in body.users:
        user = db.get(User, item.id)
        if user is None or user.organization_id != org.id:
            raise HTTPException(404, "Usuario no encontrado")
        user.display_name = item.display_name.strip()
        if item.email is not None:
            user.email = item.email.strip() or None
    wanted = {*body.areas, "ops"}
    users = _active_users(db)
    for key in orgsvc.AREA_TEMPLATES:
        if key in wanted or orgsvc.template_area(db, key) is not None:
            area = _set_template_enabled(db, principal, key, key in wanted, ip)
            if key in wanted:  # permisos iniciales: todos los socios activos, con el rol de la plantilla
                orgsvc.add_default_members(db, area, key, users)
    first = org.setup_completed_at is None
    org.setup_completed_at = org.setup_completed_at or now_utc()
    org.version += 1
    audit.record(
        db,
        "org.setup_completed" if first else "org.setup_updated",
        actor_id=principal.id,
        target_type="organization",
        target_id=org.id,
        ip=ip,
        data={"changes": changes, "areas": sorted(wanted), "users": len(body.users)},
    )
    emit(db, "org.updated", everyone=True)
    db.commit()
    return {**orgsvc.org_full(org), "templates": orgsvc.templates_out(db, org.id)}
