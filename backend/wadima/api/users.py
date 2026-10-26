from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima import audit
from wadima.api.serial import user_private, user_public
from wadima.db import get_db
from wadima.models import AuthSession, User
from wadima.realtime import emit, hub
from wadima.security.auth import (
    Principal,
    client_ip,
    current_principal,
    require_admin,
    require_password_ok,
    revoke_session,
)
from wadima.security.crypto import hash_password, random_token
from wadima.services.visits import leave_area

router = APIRouter(prefix="/api/users", tags=["users"])

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,39}$")
HEX_COLOR = r"^#[0-9a-fA-F]{6}$"


class AvatarIn(BaseModel):
    skin: str = Field(default="#f1c7a1", pattern=HEX_COLOR)
    hair: str = Field(default="#3b2a20", pattern=HEX_COLOR)
    shirt: str = Field(default="#3f7cd8", pattern=HEX_COLOR)
    pants: str = Field(default="#2d3142", pattern=HEX_COLOR)
    style: int = Field(default=0, ge=0, le=3)


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    email: str | None = Field(default=None, max_length=200)
    preferences: dict | None = None
    avatar: AvatarIn | None = None


class CreateUserIn(BaseModel):
    username: str
    display_name: str = Field(min_length=1, max_length=80)
    role: str = Field(default="member", pattern="^(admin|member)$")
    email: str | None = None


class AdminUpdateIn(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|member)$")
    is_active: bool | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=80)


@router.get("")
def list_users(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    users = db.execute(select(User).order_by(User.created_at, User.username)).scalars().all()
    return [user_public(u) for u in users]


@router.patch("/me")
def update_me(body: ProfileIn, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    user = db.get(User, principal.id)
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.email is not None:
        user.email = body.email.strip() or None
    if body.preferences is not None:
        if len(str(body.preferences)) > 4000:
            raise HTTPException(422, "Preferencias demasiado grandes")
        user.preferences = body.preferences
    if body.avatar is not None:
        user.avatar = body.avatar.model_dump()
    emit(db, "presence.changed", everyone=True, user_id=str(user.id))
    db.commit()
    return user_private(user)


@router.post("")
def create_user(
    body: CreateUserIn, request: Request, admin: Principal = Depends(require_admin), db: Session = Depends(get_db)
):
    username = body.username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(422, "Usuario: 3-40 caracteres en minúscula, números, '.', '_' o '-'")
    if db.execute(select(User).where(User.username == username)).scalar_one_or_none():
        raise HTTPException(409, "El usuario ya existe")
    temp = random_token(12)
    user = User(
        username=username,
        display_name=body.display_name,
        role=body.role,
        email=body.email,
        password_hash=hash_password(temp),
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    audit.record(
        db,
        "user.created",
        actor_id=admin.id,
        target_type="user",
        target_id=user.id,
        ip=client_ip(request),
        data={"username": username, "role": body.role},
    )
    db.commit()
    return {"user": user_private(user), "temporary_password": temp}


@router.patch("/{user_id}")
def admin_update(
    user_id: uuid.UUID,
    body: AdminUpdateIn,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Usuario no encontrado")
    changes = body.model_dump(exclude_none=True)
    if user.id == admin.id and (changes.get("role") == "member" or changes.get("is_active") is False):
        raise HTTPException(400, "No puede quitarse a sí mismo el rol de administrador ni desactivarse")
    for k, v in changes.items():
        setattr(user, k, v)
    revoked = []
    if changes.get("is_active") is False:
        for s in db.execute(
            select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        ).scalars():
            leave_area(db, s, "user_disabled", client_ip(request))
            revoke_session(db, s, "user_disabled")
            revoked.append(s.id)
    audit.record(
        db,
        "user.updated",
        actor_id=admin.id,
        target_type="user",
        target_id=user.id,
        ip=client_ip(request),
        data=changes,
    )
    db.commit()
    for sid in revoked:
        hub.close_session(sid)
    return user_private(user)


@router.post("/{user_id}/reset-password")
def admin_reset_password(
    user_id: uuid.UUID, request: Request, admin: Principal = Depends(require_admin), db: Session = Depends(get_db)
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Usuario no encontrado")
    temp = random_token(12)
    user.password_hash = hash_password(temp)
    user.must_change_password = True
    sessions = (
        db.execute(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)))
        .scalars()
        .all()
    )
    for s in sessions:
        leave_area(db, s, "password_reset", client_ip(request))
        revoke_session(db, s, "password_reset")
    audit.record(
        db, "user.password_reset", actor_id=admin.id, target_type="user", target_id=user.id, ip=client_ip(request)
    )
    db.commit()
    for s in sessions:
        hub.close_session(s.id)
    return {"temporary_password": temp}
