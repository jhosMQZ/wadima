"""Control horario: jornadas (entrada/salida) y su historial de eventos, ambos protegidos por triggers.

La hora de entrada y la de una salida normal las pone el servidor de base de datos. Una salida declarada
(olvido) o fijada por un aprobador queda marcada con su tipo y su motivo y nunca se confunde con una salida
registrada en el momento. Ninguna fila se borra; time_events es de solo inserción.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from wadima.models.base import Base, created_at_col, uuid_pk
from wadima.models.org import DEFAULT_ORG_ID

CLOSE_KINDS = ("manual", "auto", "declared", "admin")
APPROVAL = ("pending", "approved", "rejected")
EVENT_KINDS = (
    "clock_in",
    "clock_out",
    "auto_close",
    "declared_close",
    "admin_close",
    "flag_pending",
    "approve",
    "reject",
    "duplicate_blocked",
)


class TimeEntry(Base):
    __tablename__ = "time_entries"
    __table_args__ = (
        CheckConstraint("close_kind IS NULL OR close_kind IN ('manual','auto','declared','admin')", name="close_kind"),
        CheckConstraint("approval_status IN ('pending','approved','rejected')", name="approval"),
        CheckConstraint("clock_out_at IS NULL OR clock_out_at >= clock_in_at", name="order"),
        CheckConstraint("(clock_out_at IS NULL) = (close_kind IS NULL)", name="closed_kind"),
        # Una sola jornada abierta por persona: impide entradas duplicadas aunque lleguen a la vez.
        Index("uq_time_entries_open", "user_id", unique=True, postgresql_where=text("clock_out_at IS NULL")),
        Index("ix_time_entries_user_in", "user_id", "clock_in_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), default=DEFAULT_ORG_ID
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    clock_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )
    clock_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_kind: Mapped[str | None] = mapped_column(String(10))
    in_source: Mapped[str] = mapped_column(String(20), nullable=False, default="web")
    out_source: Mapped[str | None] = mapped_column(String(20))
    in_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth_sessions.id", ondelete="SET NULL"))
    out_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("auth_sessions.id", ondelete="SET NULL"))
    in_ip: Mapped[str | None] = mapped_column(String(64))
    out_ip: Mapped[str | None] = mapped_column(String(64))
    in_note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    out_note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    close_reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    pending_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_reason: Mapped[str | None] = mapped_column(String(40))
    approval_status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    in_audit_id: Mapped[int | None] = mapped_column(BigInteger)
    out_audit_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = created_at_col()


class TimeEvent(Base):
    """Historial verificable: quién, qué, cuándo (hora del servidor) y desde dónde. Solo inserción."""

    __tablename__ = "time_events"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('clock_in','clock_out','auto_close','declared_close','admin_close','flag_pending',"
            "'approve','reject','duplicate_blocked')",
            name="kind",
        ),
        Index("ix_time_events_user_at", "user_id", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("time_entries.id", ondelete="RESTRICT"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="web")
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    audit_id: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
