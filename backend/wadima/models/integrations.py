"""Google Drive (conexiones, mapa de sincronización, bitácora), credenciales de IA y Project Studio."""

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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wadima.models.base import Base, created_at_col, updated_at_col, uuid_pk


class DriveConnection(Base):
    """Conexión OAuth con Google Drive. scope='workspace' es el almacenamiento central (una sola);
    scope='user' son conexiones personales opcionales. Los tokens se guardan cifrados."""

    __tablename__ = "drive_connections"
    __table_args__ = (
        CheckConstraint("scope IN ('workspace','user')", name="scope"),
        CheckConstraint("status IN ('pending','connected','error','revoked','disconnected')", name="status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    google_email: Mapped[str | None] = mapped_column(String(200))
    google_name: Mapped[str | None] = mapped_column(String(200))
    token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    granted_scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    root_folder_id: Mapped[str | None] = mapped_column(String(120))
    folders: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    changes_page_token: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(14), nullable=False, default="pending")
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class OAuthState(Base):
    """Estado transitorio de un flujo OAuth (anti-CSRF + verificador PKCE cifrado)."""

    __tablename__ = "oauth_states"

    id: Mapped[uuid.UUID] = uuid_pk()
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    verifier_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class DriveFile(Base):
    """Correspondencia entre un objeto local y su archivo en Drive (ID estable de Drive)."""

    __tablename__ = "drive_files"
    __table_args__ = (
        CheckConstraint(
            "sync_state IN ('pending','uploading','synced','error','conflict','remote_deleted')",
            name="sync_state",
        ),
        UniqueConstraint("local_kind", "local_id", name="local_ref"),
        Index("ix_drive_files_state", "sync_state", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    connection_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("drive_connections.id", ondelete="SET NULL"))
    local_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    local_id: Mapped[str] = mapped_column(String(80), nullable=False)
    area_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    folder_key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    local_path: Mapped[str | None] = mapped_column(String(400))
    local_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    local_md5: Mapped[str] = mapped_column(String(32), nullable=False)
    drive_file_id: Mapped[str | None] = mapped_column(String(120), index=True)
    drive_parent_id: Mapped[str | None] = mapped_column(String(120))
    drive_md5: Mapped[str | None] = mapped_column(String(32))
    drive_version: Mapped[str | None] = mapped_column(String(40))
    drive_modified_time: Mapped[str | None] = mapped_column(String(40))
    base_md5: Mapped[str | None] = mapped_column(String(32))  # md5 sincronizado por última vez
    upload_session_uri: Mapped[str | None] = mapped_column(Text)  # subida reanudable en curso
    conflict: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sync_state: Mapped[str] = mapped_column(String(14), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    next_attempt_at: Mapped[datetime] = created_at_col()
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class SyncLog(Base):
    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = created_at_col()
    drive_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("drive_files.id", ondelete="SET NULL"), index=True
    )
    op: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[str] = mapped_column(String(12), nullable=False)  # ok | retry | error | conflict
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")


class AICredential(Base):
    """Credencial de IA de un socio. Nunca se comparte ni se devuelve al cliente."""

    __tablename__ = "ai_credentials"
    __table_args__ = (
        CheckConstraint("status IN ('unverified','verified','error','revoked')", name="status"),
        UniqueConstraint("user_id", "provider", name="user_provider"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)  # anthropic_api
    label: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_hint: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="unverified")
    default_model: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    account_info: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rate_limits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    allow_memory_agent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()


class MCPToken(Base):
    """Token personal para que el cliente oficial de Claude del socio (Claude Code o Claude Desktop, con su
    propia suscripción) consulte WADIMA mediante MCP. Solo se guarda su SHA-256; se muestra una vez."""

    __tablename__ = "mcp_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    token_hint: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_client: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AIRun(Base):
    """Ejecución de un agente de IA. Privada del usuario salvo que la comparta explícitamente."""

    __tablename__ = "ai_runs"
    __table_args__ = (Index("ix_ai_runs_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_credentials.id", ondelete="SET NULL"))
    area_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("areas.id", ondelete="SET NULL"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    context_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="running")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = created_at_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudioProject(Base):
    __tablename__ = "studio_projects"
    __table_args__ = (UniqueConstraint("slug", name="slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    area_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    template: Mapped[str] = mapped_column(String(30), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudioRun(Base):
    __tablename__ = "studio_runs"
    __table_args__ = (Index("ix_studio_runs_project", "studio_project_id", "started_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    studio_project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("studio_projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    profile: Mapped[str] = mapped_column(String(30), nullable=False)
    command: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="running")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    port: Mapped[int | None] = mapped_column(Integer)
    pid: Mapped[int | None] = mapped_column(Integer)
    log_tail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime] = created_at_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
