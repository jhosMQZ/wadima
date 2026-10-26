"""Biblioteca documental, versiones, fragmentos indexados, resúmenes y cola de trabajos."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from wadima.models.base import Base, created_at_col, updated_at_col, uuid_pk

DOC_STATUS = (
    "received",
    "validated",
    "processing",
    "converted",
    "indexed",
    "needs_review",
    "error",
)

TSV_CHUNK = (
    "setweight(to_tsvector('simple', wadima_unaccent(coalesce(heading, ''))), 'A') || "
    "setweight(to_tsvector('simple', wadima_unaccent(content)), 'B')"
)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_area_created", "area_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    filename: Mapped[str] = mapped_column(String(250), nullable=False)
    ext: Mapped[str] = mapped_column(String(12), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False, default="general")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(14), nullable=False, default="received")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="doc_version"),
        Index("ix_document_versions_sha256", "sha256"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    blob_path: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(14), nullable=False, default="received")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    markdown: Mapped[str | None] = mapped_column(Text)
    plain_text: Mapped[str | None] = mapped_column(Text)
    tables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    suspicious: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("ix_document_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_document_chunks_version", "version_id", "ord"),
        Index("ix_document_chunks_hash", "content_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    locator: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # páginas, hoja, sección
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suspicious: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed(TSV_CHUNK, persisted=True))


class DocumentSummary(Base):
    __tablename__ = "document_summaries"
    __table_args__ = (UniqueConstraint("version_id", "level", name="version_level"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False)  # short | sections | outline
    content: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False, default="extractive")
    created_at: Mapped[datetime] = created_at_col()


class Job(Base):
    """Cola de trabajos persistente (SELECT ... FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','done','error','dead')", name="status"),
        Index("ix_jobs_pick", "status", "run_after"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    run_after: Mapped[datetime] = created_at_col()
    locked_by: Mapped[str | None] = mapped_column(String(60))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
