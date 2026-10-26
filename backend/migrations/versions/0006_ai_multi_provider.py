"""varios proveedores de IA por socio: dirección propia para las APIs compatibles con OpenAI

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-26 04:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_credentials", sa.Column("base_url", sa.String(length=300), nullable=False, server_default=""))
    # Los identificadores de modelo de OpenRouter y similares superan los 60 caracteres.
    op.alter_column("ai_credentials", "default_model", type_=sa.String(length=200), existing_nullable=False)
    op.alter_column("ai_runs", "model", type_=sa.String(length=200), existing_nullable=False)


def downgrade() -> None:
    op.alter_column("ai_runs", "model", type_=sa.String(length=60), existing_nullable=False)
    op.alter_column("ai_credentials", "default_model", type_=sa.String(length=60), existing_nullable=False)
    op.drop_column("ai_credentials", "base_url")
