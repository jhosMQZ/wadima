"""Proyectos, tareas, notas Markdown versionadas, comentarios, decisiones, actividad y notificaciones."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from wadima.models.base import Base, created_at_col, updated_at_col, uuid_pk


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("area_id", "slug", name="area_slug"),
        CheckConstraint("status IN ('planned','active','paused','done','archived')", name="status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_summary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="project_user"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="member")  # lead | member


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status IN ('todo','in_progress','blocked','done')", name="status"),
        Index("ix_tasks_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="todo")
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # IA asignada: el Claude del socio indicado (conectado por MCP con su propia suscripción).
    ai_assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    due_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Note(Base):
    """Documento Markdown editable (especificación, nota, requisito, plan...).
    Concurrencia optimista mediante `version`; cada guardado crea una NoteRevision."""

    __tablename__ = "notes"
    __table_args__ = (CheckConstraint("kind IN ('note','spec','requirement','plan','doc','meeting')", name="kind"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="note")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NoteRevision(Base):
    __tablename__ = "note_revisions"
    __table_args__ = (UniqueConstraint("note_id", "version", name="note_version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    note_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (Index("ix_comments_target", "target_type", "target_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)  # project|task|note|document
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (CheckConstraint("status IN ('proposed','approved','rejected','superseded')", name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="proposed")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class ActivityEvent(Base):
    """Actividad de colaboración registrada a partir de acciones reales (nunca inventada)."""

    __tablename__ = "activity_events"
    __table_args__ = (
        Index("ix_activity_area_ts", "area_id", "ts"),
        Index("ix_activity_project_ts", "project_id", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = created_at_col()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_kind: Mapped[str] = mapped_column(String(10), nullable=False, default="user")  # user|agent|system
    actor_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    area_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(String(400), nullable=False)
    ref_type: Mapped[str | None] = mapped_column(String(20))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    link: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_at: Mapped[datetime] = created_at_col()
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
