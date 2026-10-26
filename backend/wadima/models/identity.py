"""Usuarios, sesiones, intentos de login, áreas, membresías, invitaciones y registros de entrada."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from wadima.models.base import Base, created_at_col, updated_at_col, uuid_pk
from wadima.models.org import DEFAULT_ORG_ID

ROLES = ("admin", "member")
AREA_ROLES = ("owner", "editor", "viewer")
PRESENCE = ("online", "working", "meeting", "paused", "offline")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin','member')", name="role"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, default=DEFAULT_ORG_ID
    )
    username: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="member")
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    avatar: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class AuthSession(Base):
    """Sesión de navegador. Solo se guarda el hash SHA-256 del token de la cookie."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = created_at_col()
    last_seen_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(40))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    active_area_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("areas.id", ondelete="SET NULL"))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_username_ts", "username", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(40), nullable=False)
    ip: Mapped[str | None] = mapped_column(INET)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ts: Mapped[datetime] = created_at_col()


class Area(Base):
    __tablename__ = "areas"
    __table_args__ = (
        Index(
            "uq_areas_template_active",
            "organization_id",
            "template_key",
            unique=True,
            postgresql_where=text("template_key IS NOT NULL AND archived_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, default=DEFAULT_ORG_ID
    )
    room_key: Mapped[str] = mapped_column(String(30), nullable=False, default="ops")
    color: Mapped[str] = mapped_column(String(9), nullable=False, default="#5b8def")
    # Plantilla predefinida de la que procede (dev, library, ai, meeting, admin, servers, ops) o None si es
    # personalizada. Una plantilla solo puede tener un área activa: evita módulos duplicados.
    template_key: Mapped[str | None] = mapped_column(String(30))
    icon: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AreaMember(Base):
    __tablename__ = "area_members"
    __table_args__ = (
        UniqueConstraint("area_id", "user_id", name="area_user"),
        CheckConstraint("role IN ('owner','editor','viewer')", name="role"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="editor")
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()


class Invitation(Base):
    """Código de invitación a un área. Solo se almacena el hash del código."""

    __tablename__ = "invitations"
    __table_args__ = (CheckConstraint("role IN ('editor','viewer')", name="role"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code_hint: Mapped[str] = mapped_column(String(12), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="editor")
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()


class AccessRequest(Base):
    __tablename__ = "access_requests"
    __table_args__ = (CheckConstraint("status IN ('pending','approved','rejected','auto')", name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invitations.id", ondelete="SET NULL"))
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class AreaVisit(Base):
    """Registro horario de entrada/salida a un área. entered_at es la hora del servidor (BD)
    y no puede modificarse; left_at solo puede fijarse una vez (garantizado por trigger)."""

    __tablename__ = "area_visits"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="RESTRICT"), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth_sessions.id", ondelete="SET NULL"))
    entered_at: Mapped[datetime] = created_at_col()
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(30))
    entry_audit_id: Mapped[int | None] = mapped_column(BigInteger)
    exit_audit_id: Mapped[int | None] = mapped_column(BigInteger)


class UserStatus(Base):
    """Estado declarado del usuario. 'online/offline' lo deriva el hub de WebSockets;
    'working/meeting/paused' los declara el propio usuario. No se infiere productividad."""

    __tablename__ = "user_status"
    __table_args__ = (CheckConstraint("declared IN ('available','working','meeting','paused')", name="declared"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    declared: Mapped[str] = mapped_column(String(12), nullable=False, default="available")
    message: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    area_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("areas.id", ondelete="SET NULL"))
    room_key: Mapped[str | None] = mapped_column(String(30))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = updated_at_col()


class AuditLog(Base):
    """Bitácora de auditoría encadenada por hash (SHA-256). Inmutable: un trigger impide
    UPDATE/DELETE. La verificación recalcula la cadena completa."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64))
    area_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
