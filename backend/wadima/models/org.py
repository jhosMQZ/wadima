"""Organización: identidad configurable (nombre comercial, logotipo, colores), zona horaria y políticas.

Hoy hay una sola organización por instalación; usuarios y áreas ya la referencian para poder separar los
datos de varias organizaciones en el futuro. Los identificadores técnicos (paquete, carpeta raíz de Drive,
variables WADIMA_*) no dependen del nombre comercial.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wadima.models.base import Base, created_at_col, updated_at_col, uuid_pk

DEFAULT_ORG_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    platform_name: Mapped[str] = mapped_column(String(60), nullable=False, default="WADIMA")
    legal_name: Mapped[str] = mapped_column(String(160), nullable=False, default="WADIMA INDUSTRIE")
    short_mark: Mapped[str] = mapped_column(String(4), nullable=False, default="W")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    primary_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#6fd3ff")
    secondary_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#ffd166")
    logo: Mapped[bytes | None] = mapped_column(LargeBinary)
    logo_mime: Mapped[str | None] = mapped_column(String(40))
    logo_sha256: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Bogota")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    setup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
