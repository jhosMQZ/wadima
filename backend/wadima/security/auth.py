"""Sesiones de servidor con cookie httpOnly, CSRF por cabecera y comprobación de Origin."""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wadima.config import get_settings
from wadima.db import get_db
from wadima.models import AuthSession, LoginAttempt, User
from wadima.security.crypto import constant_eq, random_token, sha256_hex

COOKIE = "wadima_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    user: User
    session: AuthSession

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"


def client_ip(request: Request | WebSocket) -> str | None:
    """IP del cliente directo (no se confía en X-Forwarded-For). None si no es una IP válida."""
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def now_utc() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- rate limiting
def login_blocked(db: Session, username: str, ip: str | None) -> bool:
    s = get_settings()
    since = now_utc() - timedelta(minutes=s.login_window_minutes)
    failures = db.execute(
        select(func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.username == username, LoginAttempt.success.is_(False), LoginAttempt.ts > since)
    ).scalar_one()
    return failures >= s.login_max_failures


def record_attempt(db: Session, username: str, ip: str | None, success: bool) -> None:
    db.add(LoginAttempt(username=username, ip=ip, success=success))


# --------------------------------------------------------------------------- sesiones
def create_session(db: Session, user: User, ip: str | None, user_agent: str | None) -> tuple[str, AuthSession]:
    s = get_settings()
    token = random_token(32)
    sess = AuthSession(
        token_hash=sha256_hex(token),
        csrf_token=random_token(24),
        user_id=user.id,
        expires_at=now_utc() + timedelta(hours=s.session_absolute_hours),
        ip=ip,
        user_agent=(user_agent or "")[:300],
    )
    db.add(sess)
    db.flush()
    return token, sess


def set_session_cookie(response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        max_age=s.session_absolute_hours * 3600,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/")


def resolve_session(db: Session, token: str | None, touch: bool = True) -> Principal | None:
    if not token:
        return None
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == sha256_hex(token))).scalar_one_or_none()
    if sess is None or sess.revoked_at is not None:
        return None
    now = now_utc()
    s = get_settings()
    if sess.expires_at <= now or sess.last_seen_at + timedelta(minutes=s.session_idle_minutes) <= now:
        if sess.revoked_at is None:
            sess.revoked_at = now
            sess.revoke_reason = "expired"
            db.commit()
        return None
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        return None
    if touch and (now - sess.last_seen_at).total_seconds() > 60:
        sess.last_seen_at = now
        db.commit()
    return Principal(user=user, session=sess)


def check_origin(origin: str | None) -> bool:
    if not origin:
        return True  # clientes no navegador (tests, CLI); el CSRF token sigue siendo obligatorio
    allowed = set(get_settings().allowed_origins)
    return origin.rstrip("/") in allowed


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    principal = resolve_session(db, request.cookies.get(COOKIE))
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión no válida o expirada")
    if request.method not in SAFE_METHODS:
        if not check_origin(request.headers.get("origin")):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Origen no permitido")
        token = request.headers.get(CSRF_HEADER, "")
        if not token or not constant_eq(token, principal.session.csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Token CSRF ausente o inválido")
    request.state.principal = principal
    return principal


def require_password_ok(principal: Principal = Depends(current_principal)) -> Principal:
    """Bloquea el uso de la API hasta cambiar la contraseña inicial."""
    if principal.user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Debe cambiar la contraseña inicial")
    return principal


def require_admin(principal: Principal = Depends(require_password_ok)) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requiere rol de administrador")
    return principal


def revoke_session(db: Session, sess: AuthSession, reason: str) -> None:
    if sess.revoked_at is None:
        sess.revoked_at = now_utc()
        sess.revoke_reason = reason


def validate_new_password(password: str, username: str) -> None:
    if len(password) < 10:
        raise HTTPException(422, "La contraseña debe tener al menos 10 caracteres")
    if username.lower() in password.lower():
        raise HTTPException(422, "La contraseña no puede contener el nombre de usuario")
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        raise HTTPException(422, "Use al menos tres tipos de caracteres (minúsculas, mayúsculas, números, símbolos)")
