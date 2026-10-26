from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wadima import audit
from wadima.activity import log_activity, notify
from wadima.api.serial import area_out, iso, user_public
from wadima.db import get_db
from wadima.models import (
    AccessRequest,
    Area,
    AreaMember,
    AreaVisit,
    AuthSession,
    Invitation,
    User,
)
from wadima.office import ROOM_KEYS
from wadima.realtime import emit
from wadima.security.auth import Principal, client_ip, now_utc, require_password_ok
from wadima.security.crypto import random_token, sha256_hex
from wadima.security.perms import RANK, accessible_area_ids, area_role, require_area
from wadima.services.visits import close_visit, enter_area, leave_area

router = APIRouter(prefix="/api", tags=["areas"])

ROOM_PATTERN = "^(" + "|".join(ROOM_KEYS) + ")$"


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
    return slug[:60] or "area"


def unique_slug(db: Session, base: str) -> str:
    slug, n = base, 2
    while db.execute(select(Area.id).where(Area.slug == slug)).first():
        slug = f"{base[:55]}-{n}"
        n += 1
    return slug


class AreaIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=2000)
    room_key: str = Field(default="ops", pattern=ROOM_PATTERN)
    color: str = Field(default="#5b8def", pattern=r"^#[0-9a-fA-F]{6}$")


class AreaPatch(BaseModel):
    version: int
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    room_key: str | None = Field(default=None, pattern=ROOM_PATTERN)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class ConfirmIn(BaseModel):
    confirm_slug: str


class MemberIn(BaseModel):
    role: str = Field(pattern="^(owner|editor|viewer)$")


class InvitationIn(BaseModel):
    role: str = Field(default="editor", pattern="^(editor|viewer)$")
    expires_hours: int = Field(default=72, ge=1, le=720)
    max_uses: int = Field(default=1, ge=1, le=20)
    requires_approval: bool = False
    note: str = Field(default="", max_length=200)


class RedeemIn(BaseModel):
    code: str = Field(min_length=6, max_length=40)


class DecideIn(BaseModel):
    approve: bool


def _owners(db: Session, area_id: uuid.UUID) -> set[uuid.UUID]:
    owners = set(
        db.execute(
            select(AreaMember.user_id).where(AreaMember.area_id == area_id, AreaMember.role == "owner")
        ).scalars()
    )
    admins = set(db.execute(select(User.id).where(User.role == "admin", User.is_active)).scalars())
    return owners | admins


def normalize_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", code.upper())


# --------------------------------------------------------------------------- áreas
@router.get("/areas")
def list_areas(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    ids = accessible_area_ids(db, principal)
    areas = db.execute(select(Area).where(Area.id.in_(ids)).order_by(Area.name)).scalars().all() if ids else []
    counts = (
        dict(
            db.execute(
                select(AreaMember.area_id, func.count()).where(AreaMember.area_id.in_(ids)).group_by(AreaMember.area_id)
            ).all()
        )
        if ids
        else {}
    )
    pending = db.execute(
        select(AccessRequest, Area.name)
        .join(Area, Area.id == AccessRequest.area_id)
        .where(AccessRequest.user_id == principal.id, AccessRequest.status == "pending")
    ).all()
    return {
        "areas": [{**area_out(a, area_role(db, principal, a.id)), "member_count": counts.get(a.id, 0)} for a in areas],
        "pending_requests": [
            {"id": str(r.id), "area_name": name, "created_at": iso(r.created_at)} for r, name in pending
        ],
    }


@router.post("/areas")
def create_area(
    body: AreaIn, request: Request, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    area = Area(
        slug=unique_slug(db, slugify(body.name)),
        name=body.name.strip(),
        description=body.description,
        room_key=body.room_key,
        color=body.color,
        created_by=principal.id,
    )
    db.add(area)
    db.flush()
    db.add(AreaMember(area_id=area.id, user_id=principal.id, role="owner", added_by=principal.id))
    audit.record(
        db,
        "area.created",
        actor_id=principal.id,
        target_type="area",
        target_id=area.id,
        area_id=area.id,
        ip=client_ip(request),
        data={"name": area.name},
    )
    log_activity(db, "area.created", f"Creó el área «{area.name}»", user=principal.user, area_id=area.id)
    db.commit()
    return area_out(area, "owner")


@router.get("/areas/{area_id}")
def get_area(area_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    area = require_area(db, principal, area_id)
    members = db.execute(
        select(AreaMember, User).join(User, User.id == AreaMember.user_id).where(AreaMember.area_id == area_id)
    ).all()
    return {
        **area_out(area, area_role(db, principal, area_id)),
        "members": [{**user_public(u), "area_role": m.role, "since": iso(m.created_at)} for m, u in members],
    }


@router.patch("/areas/{area_id}")
def update_area(
    area_id: uuid.UUID,
    body: AreaPatch,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    area = require_area(db, principal, area_id, "owner")
    if body.version != area.version:
        raise HTTPException(409, {"message": "El área fue modificada por otra persona", "current": area_out(area)})
    changes = body.model_dump(exclude_none=True, exclude={"version"})
    for k, v in changes.items():
        setattr(area, k, v)
    area.version += 1
    audit.record(
        db,
        "area.updated",
        actor_id=principal.id,
        target_type="area",
        target_id=area.id,
        area_id=area.id,
        ip=client_ip(request),
        data=changes,
    )
    emit(db, "area.updated", area_id=area.id, id=str(area.id))
    db.commit()
    return area_out(area, area_role(db, principal, area_id))


@router.post("/areas/{area_id}/archive")
def archive_area(
    area_id: uuid.UUID,
    body: ConfirmIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    """Eliminación segura: requiere escribir el identificador; el área se archiva (recuperable por admin)."""
    area = require_area(db, principal, area_id, "owner")
    if body.confirm_slug != area.slug:
        raise HTTPException(422, "La confirmación no coincide con el identificador del área")
    ip = client_ip(request)
    for visit in db.execute(
        select(AreaVisit).where(AreaVisit.area_id == area_id, AreaVisit.left_at.is_(None))
    ).scalars():
        close_visit(db, visit, "area_archived", ip)
    for sess in db.execute(select(AuthSession).where(AuthSession.active_area_id == area_id)).scalars():
        sess.active_area_id = None
    area.archived_at = now_utc()
    area.version += 1
    audit.record(
        db, "area.archived", actor_id=principal.id, target_type="area", target_id=area.id, area_id=area.id, ip=ip
    )
    emit(db, "area.updated", area_id=area.id, id=str(area.id))
    db.commit()
    return {"ok": True, "archived_at": iso(area.archived_at)}


@router.post("/areas/{area_id}/restore")
def restore_area(
    area_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    if not principal.is_admin:
        raise HTTPException(403, "Solo un administrador puede restaurar áreas")
    area = db.get(Area, area_id)
    if area is None:
        raise HTTPException(404, "Área no encontrada")
    area.archived_at = None
    area.version += 1
    audit.record(
        db,
        "area.restored",
        actor_id=principal.id,
        target_type="area",
        target_id=area.id,
        area_id=area.id,
        ip=client_ip(request),
    )
    db.commit()
    return area_out(area, "owner")


@router.get("/areas-archived")
def archived_areas(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    if not principal.is_admin:
        raise HTTPException(403, "Solo administradores")
    return [area_out(a) for a in db.execute(select(Area).where(Area.archived_at.is_not(None))).scalars()]


# --------------------------------------------------------------------------- miembros
@router.put("/areas/{area_id}/members/{user_id}")
def set_member(
    area_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    require_area(db, principal, area_id, "owner")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(404, "Usuario no encontrado")
    m = db.execute(
        select(AreaMember).where(AreaMember.area_id == area_id, AreaMember.user_id == user_id)
    ).scalar_one_or_none()
    if m is None:
        m = AreaMember(area_id=area_id, user_id=user_id, role=body.role, added_by=principal.id)
        db.add(m)
    else:
        if m.role == "owner" and body.role != "owner":
            _ensure_other_owner(db, area_id, user_id)
        m.role = body.role
    audit.record(
        db,
        "area.member_set",
        actor_id=principal.id,
        target_type="user",
        target_id=user_id,
        area_id=area_id,
        ip=client_ip(request),
        data={"role": body.role},
    )
    emit(db, "area.updated", area_id=area_id, id=str(area_id))
    db.commit()
    return {"ok": True}


def _ensure_other_owner(db: Session, area_id: uuid.UUID, user_id: uuid.UUID) -> None:
    others = db.execute(
        select(func.count())
        .select_from(AreaMember)
        .where(AreaMember.area_id == area_id, AreaMember.role == "owner", AreaMember.user_id != user_id)
    ).scalar_one()
    if others == 0:
        raise HTTPException(400, "El área debe conservar al menos un propietario")


@router.delete("/areas/{area_id}/members/{user_id}")
def remove_member(
    area_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    if user_id != principal.id:
        require_area(db, principal, area_id, "owner")
    m = db.execute(
        select(AreaMember).where(AreaMember.area_id == area_id, AreaMember.user_id == user_id)
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, "No es miembro")
    if m.role == "owner":
        _ensure_other_owner(db, area_id, user_id)
    ip = client_ip(request)
    for visit in db.execute(
        select(AreaVisit).where(AreaVisit.area_id == area_id, AreaVisit.user_id == user_id, AreaVisit.left_at.is_(None))
    ).scalars():
        close_visit(db, visit, "membership_removed", ip)
    for sess in db.execute(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.active_area_id == area_id)
    ).scalars():
        sess.active_area_id = None
    db.delete(m)
    audit.record(
        db, "area.member_removed", actor_id=principal.id, target_type="user", target_id=user_id, area_id=area_id, ip=ip
    )
    emit(db, "area.updated", area_id=area_id, id=str(area_id))
    emit(db, "area.updated", audience={user_id}, id=str(area_id))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- invitaciones
def _invitation_out(inv: Invitation) -> dict:
    now = now_utc()
    state = (
        "revoked"
        if inv.revoked_at
        else "expired"
        if inv.expires_at <= now
        else "exhausted"
        if inv.uses >= inv.max_uses
        else "active"
    )
    return {
        "id": str(inv.id),
        "area_id": str(inv.area_id),
        "code_hint": inv.code_hint,
        "role": inv.role,
        "requires_approval": inv.requires_approval,
        "max_uses": inv.max_uses,
        "uses": inv.uses,
        "note": inv.note,
        "expires_at": iso(inv.expires_at),
        "revoked_at": iso(inv.revoked_at),
        "created_at": iso(inv.created_at),
        "state": state,
    }


@router.post("/areas/{area_id}/invitations")
def create_invitation(
    area_id: uuid.UUID,
    body: InvitationIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    require_area(db, principal, area_id, "owner")
    raw = normalize_code(random_token(16))[:12]
    while len(raw) < 12:
        raw += normalize_code(random_token(8))
    raw = raw[:12]
    code = f"WAD-{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"
    inv = Invitation(
        area_id=area_id,
        code_hash=sha256_hex(normalize_code(code)),
        code_hint=f"…{raw[-4:]}",
        role=body.role,
        requires_approval=body.requires_approval,
        max_uses=body.max_uses,
        note=body.note,
        expires_at=now_utc() + timedelta(hours=body.expires_hours),
        created_by=principal.id,
    )
    db.add(inv)
    db.flush()
    audit.record(
        db,
        "invitation.created",
        actor_id=principal.id,
        target_type="invitation",
        target_id=inv.id,
        area_id=area_id,
        ip=client_ip(request),
        data={"role": body.role, "max_uses": body.max_uses, "expires_hours": body.expires_hours},
    )
    db.commit()
    return {"invitation": _invitation_out(inv), "code": code}


@router.get("/areas/{area_id}/invitations")
def list_invitations(
    area_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    require_area(db, principal, area_id, "owner")
    rows = db.execute(
        select(Invitation).where(Invitation.area_id == area_id).order_by(Invitation.created_at.desc())
    ).scalars()
    return [_invitation_out(i) for i in rows]


@router.post("/invitations/{inv_id}/revoke")
def revoke_invitation(
    inv_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    inv = db.get(Invitation, inv_id)
    if inv is None:
        raise HTTPException(404, "Invitación no encontrada")
    require_area(db, principal, inv.area_id, "owner")
    if inv.revoked_at is None:
        inv.revoked_at = now_utc()
        audit.record(
            db,
            "invitation.revoked",
            actor_id=principal.id,
            target_type="invitation",
            target_id=inv.id,
            area_id=inv.area_id,
            ip=client_ip(request),
        )
    db.commit()
    return _invitation_out(inv)


@router.post("/invitations/redeem")
def redeem_invitation(
    body: RedeemIn, request: Request, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    ip = client_ip(request)
    code_hash = sha256_hex(normalize_code(body.code))
    inv = db.execute(select(Invitation).where(Invitation.code_hash == code_hash).with_for_update()).scalar_one_or_none()
    now = now_utc()
    if inv is None or inv.revoked_at or inv.expires_at <= now or inv.uses >= inv.max_uses:
        audit.record(
            db,
            "invitation.redeem_failed",
            actor_id=principal.id,
            ip=ip,
            data={"reason": "invalid" if inv is None else "unavailable"},
        )
        db.commit()
        raise HTTPException(400, "El código no es válido, ha caducado o fue revocado")
    area = db.get(Area, inv.area_id)
    if area is None or area.archived_at:
        raise HTTPException(400, "El área ya no está disponible")
    existing = db.execute(
        select(AreaMember).where(AreaMember.area_id == area.id, AreaMember.user_id == principal.id)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "Ya es miembro de esta área")
    pending = db.execute(
        select(AccessRequest).where(
            AccessRequest.area_id == area.id, AccessRequest.user_id == principal.id, AccessRequest.status == "pending"
        )
    ).scalar_one_or_none()
    if pending:
        raise HTTPException(409, "Ya tiene una solicitud pendiente para esta área")
    inv.uses += 1
    req = AccessRequest(
        invitation_id=inv.id,
        area_id=area.id,
        user_id=principal.id,
        role=inv.role,
        status="pending" if inv.requires_approval else "auto",
    )
    db.add(req)
    if not inv.requires_approval:
        db.add(AreaMember(area_id=area.id, user_id=principal.id, role=inv.role, added_by=inv.created_by))
        req.decided_at = now
    db.flush()
    audit.record(
        db,
        "invitation.redeemed",
        actor_id=principal.id,
        target_type="invitation",
        target_id=inv.id,
        area_id=area.id,
        ip=ip,
        data={"status": req.status, "role": inv.role},
    )
    if inv.requires_approval:
        notify(
            db,
            _owners(db, area.id),
            "access.request",
            f"{principal.user.display_name} solicita acceso",
            f"Área «{area.name}» con rol {inv.role}",
            f"/areas/{area.id}",
        )
    else:
        log_activity(
            db,
            "area.member_joined",
            f"Se unió al área «{area.name}» mediante invitación",
            user=principal.user,
            area_id=area.id,
        )
    db.commit()
    return {"status": req.status, "area": area_out(area, inv.role if req.status == "auto" else None)}


@router.get("/areas/{area_id}/requests")
def list_requests(
    area_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    require_area(db, principal, area_id, "owner")
    rows = db.execute(
        select(AccessRequest, User)
        .join(User, User.id == AccessRequest.user_id)
        .where(AccessRequest.area_id == area_id)
        .order_by(AccessRequest.created_at.desc())
    ).all()
    return [
        {
            "id": str(r.id),
            "user": user_public(u),
            "role": r.role,
            "status": r.status,
            "created_at": iso(r.created_at),
            "decided_at": iso(r.decided_at),
        }
        for r, u in rows
    ]


@router.post("/access-requests/{req_id}/decide")
def decide_request(
    req_id: uuid.UUID,
    body: DecideIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    req = db.get(AccessRequest, req_id)
    if req is None:
        raise HTTPException(404, "Solicitud no encontrada")
    area = require_area(db, principal, req.area_id, "owner")
    if req.status != "pending":
        raise HTTPException(409, "La solicitud ya fue resuelta")
    req.status = "approved" if body.approve else "rejected"
    req.decided_by = principal.id
    req.decided_at = now_utc()
    if body.approve:
        db.add(AreaMember(area_id=req.area_id, user_id=req.user_id, role=req.role, added_by=principal.id))
    audit.record(
        db,
        "access_request.decided",
        actor_id=principal.id,
        target_type="access_request",
        target_id=req.id,
        area_id=req.area_id,
        ip=client_ip(request),
        data={"status": req.status},
    )
    notify(
        db,
        {req.user_id},
        "access.decided",
        f"Solicitud {'aprobada' if body.approve else 'rechazada'}: {area.name}",
        link="/areas",
    )
    db.commit()
    return {"status": req.status}


# --------------------------------------------------------------------------- entrada / salida
@router.post("/areas/{area_id}/enter")
def enter(
    area_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    area = require_area(db, principal, area_id)
    if not area.enabled:
        raise HTTPException(409, "El área está deshabilitada; un administrador puede habilitarla en Áreas")
    sess = db.get(AuthSession, principal.session.id)
    visit = enter_area(db, principal.user, sess, area, client_ip(request))
    db.commit()
    return {
        "area": area_out(area, area_role(db, principal, area_id)),
        "visit": {"id": str(visit.id), "entered_at": iso(visit.entered_at)},
    }


@router.post("/areas/leave")
def leave(request: Request, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    sess = db.get(AuthSession, principal.session.id)
    visit = leave_area(db, sess, "leave", client_ip(request))
    db.commit()
    return {"visit": {"id": str(visit.id), "left_at": iso(visit.left_at)} if visit else None}


@router.get("/areas/{area_id}/visits")
def visits(
    area_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    limit: int = 100,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    require_area(db, principal, area_id)
    role = area_role(db, principal, area_id)
    q = select(AreaVisit, User).join(User, User.id == AreaVisit.user_id).where(AreaVisit.area_id == area_id)
    if RANK[role] < RANK["owner"]:
        q = q.where(AreaVisit.user_id == principal.id)
    elif user_id:
        q = q.where(AreaVisit.user_id == user_id)
    rows = db.execute(q.order_by(AreaVisit.entered_at.desc()).limit(min(limit, 500))).all()
    return [
        {
            "id": str(v.id),
            "user": user_public(u),
            "entered_at": iso(v.entered_at),
            "left_at": iso(v.left_at),
            "end_reason": v.end_reason,
            "entry_audit_id": v.entry_audit_id,
            "exit_audit_id": v.exit_audit_id,
        }
        for v, u in rows
    ]
