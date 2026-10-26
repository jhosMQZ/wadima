"""Memoria compartida: elementos por capas, relaciones (duplicados/contradicciones) e historial."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from wadima.models.base import Base, created_at_col, updated_at_col, uuid_pk

TSV_MEMORY = (
    "setweight(to_tsvector('simple', wadima_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('simple', wadima_unaccent(content)), 'B')"
)


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        CheckConstraint("layer IN ('documental','semantic','operational','consolidated')", name="layer"),
        CheckConstraint("validation IN ('unverified','inferred','confirmed','rejected')", name="validation"),
        CheckConstraint("status IN ('active','obsolete','archived','contradicted','deleted')", name="status"),
        Index("ix_memory_items_tsv", "tsv", postgresql_using="gin"),
        Index("ix_memory_items_area_layer", "area_id", "layer", "status"),
        Index("ix_memory_items_source", "source_type", "source_id"),
        Index("ix_memory_items_subject", "area_id", "subject_key"),
        Index(
            "ix_memory_items_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    layer: Mapped[str] = mapped_column(String(14), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # fact|procedure|concept|decision|...
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    subject_key: Mapped[str | None] = mapped_column(String(200))  # clave normalizada para contradicciones
    value_text: Mapped[str | None] = mapped_column(String(400))
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64))
    source_version: Mapped[int | None] = mapped_column(Integer)
    source_ref: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    validation: Mapped[str] = mapped_column(String(12), nullable=False, default="inferred")
    status: Mapped[str] = mapped_column(String(14), nullable=False, default="active")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by_agent: Mapped[str] = mapped_column(String(40), nullable=False, default="memory_agent")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed(TSV_MEMORY, persisted=True))


class MemoryRelation(Base):
    __tablename__ = "memory_relations"
    __table_args__ = (
        CheckConstraint(
            "relation IN ('contradicts','duplicates','supports','derived_from','related','supersedes')",
            name="relation",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    from_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[str] = mapped_column(String(14), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = created_at_col()


class MemoryHistory(Base):
    __tablename__ = "memory_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    memory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    change: Mapped[str] = mapped_column(String(20), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_agent: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ts: Mapped[datetime] = created_at_col()


class AgentState(Base):
    """Estado real de los agentes/servicios internos (Memory Agent, sincronizador, pipeline)."""

    __tablename__ = "agent_states"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="idle")  # idle|working|error|stopped
    current: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    last_result: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = updated_at_col()
