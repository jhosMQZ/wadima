from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima import audit
from wadima import org as orgsvc
from wadima.api.serial import area_out, iso, user_private
from wadima.db import get_db
from wadima.models import Area, AuthSession, LoginAttempt, User, UserStatus
from wadima.realtime import hub
from wadima.security.auth import (
    Principal,
    clear_session_cookie,
    client_ip,
    create_session,
    current_principal,
    login_blocked,
    now_utc,
    record_attempt,
    revoke_session,
    set_session_cookie,
    validate_new_password,
)
from wadima.security.crypto import hash_password, needs_rehash, verify_password
from wadima.security.perms import area_role
from wadima.services.visits import leave_area

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Hash ficticio para igualar el tiempo de respuesta cuando el usuario no existe.
_DUMMY_HASH = hash_password("wadima-dummy-password")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


def me_payload(db: Session, principal: Principal) -> dict:
    area = db.get(Area, principal.session.active_area_id) if principal.session.active_area_id else None
    st = db.get(UserStatus, principal.id)
    org = orgsvc.get_org(db, principal.user.organization_id)
    return {
        "org": orgsvc.org_public(org),
        # La configuración inicial la completa un administrador; los demás entran con los valores por defecto.
        "setup_required": principal.is_admin and org.setup_completed_at is None,
        "user": user_private(principal.user),
        "csrf_token": principal.session.csrf_token,
        "session": {
            "id": str(principal.session.id),
            "expires_at": iso(principal.session.expires_at),
        },
        "active_area": area_out(area, area_role(db, principal, area.id)) if area else None,
        "status": {
            "declared": st.declared if st else "available",
            "message": st.message if st else "",
            "task_id": str(st.task_id) if st and st.task_id else None,
        },
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    username = body.username.strip().lower()
    ip = client_ip(request)
    if login_blocked(db, username, ip):
        audit.record(db, "auth.login_blocked", ip=ip, data={"username": username})
        db.commit()
        raise HTTPException(429, "Demasiados intentos fallidos. Espere unos minutos.")
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    ok = verify_password(user.password_hash if user else _DUMMY_HASH, body.password)
    if not user or not ok or not user.is_active:
        record_attempt(db, username, ip, False)
        audit.record(db, "auth.login_failed", ip=ip, data={"username": username})
        db.commit()
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    record_attempt(db, username, ip, True)
    token, sess = create_session(db, user, ip, request.headers.get("user-agent"))
    user.last_login_at = now_utc()
    audit.record(db, "auth.login", actor_id=user.id, target_type="session", target_id=sess.id, ip=ip)
    db.commit()
    set_session_cookie(response, token)
    return me_payload(db, Principal(user=user, session=sess))


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    sess = db.get(AuthSession, principal.session.id)
    leave_area(db, sess, "logout", ip)
    revoke_session(db, sess, "logout")
    audit.record(db, "auth.logout", actor_id=principal.id, target_type="session", target_id=sess.id, ip=ip)
    db.commit()
    hub.close_session(sess.id)
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return me_payload(db, principal)


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    user = db.get(User, principal.id)
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(400, "La contraseña actual no es correcta")
    if body.new_password == body.current_password:
        raise HTTPException(422, "La nueva contraseña debe ser distinta")
    validate_new_password(body.new_password, user.username)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    others = (
        db.execute(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.id != principal.session.id,
                AuthSession.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for s in others:
        revoke_session(db, s, "password_changed")
    audit.record(
        db, "auth.password_changed", actor_id=user.id, ip=client_ip(request), data={"revoked_sessions": len(others)}
    )
    db.commit()
    for s in others:
        hub.close_session(s.id)
    return {"ok": True, "revoked_sessions": len(others)}


@router.get("/sessions")
def my_sessions(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = (
        db.execute(
            select(AuthSession)
            .where(AuthSession.user_id == principal.id)
            .order_by(AuthSession.created_at.desc())
            .limit(30)
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(s.id),
            "created_at": iso(s.created_at),
            "last_seen_at": iso(s.last_seen_at),
            "expires_at": iso(s.expires_at),
            "revoked_at": iso(s.revoked_at),
            "revoke_reason": s.revoke_reason,
            "ip": str(s.ip) if s.ip else None,
            "user_agent": s.user_agent,
            "current": s.id == principal.session.id,
        }
        for s in rows
    ]


@router.delete("/sessions/{session_id}")
def revoke_my_session(
    session_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    sess = db.get(AuthSession, session_id)
    if sess is None or sess.user_id != principal.id:
        raise HTTPException(404, "Sesión no encontrada")
    if sess.id == principal.session.id:
        raise HTTPException(400, "Use 'cerrar sesión' para la sesión actual")
    leave_area(db, sess, "session_revoked", client_ip(request))
    revoke_session(db, sess, "revoked_by_user")
    audit.record(
        db,
        "auth.session_revoked",
        actor_id=principal.id,
        target_type="session",
        target_id=sess.id,
        ip=client_ip(request),
    )
    db.commit()
    hub.close_session(sess.id)
    return {"ok": True}


@router.get("/history")
def login_history(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = (
        db.execute(
            select(LoginAttempt)
            .where(LoginAttempt.username == principal.user.username)
            .order_by(LoginAttempt.ts.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    return [{"ts": iso(r.ts), "ip": str(r.ip) if r.ip else None, "success": r.success} for r in rows]
