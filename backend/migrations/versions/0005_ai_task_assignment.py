"""tareas de proyecto asignadas a la IA de un socio

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25 19:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("ai_assignee_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_tasks_ai_assignee_id_users"), "tasks", "users", ["ai_assignee_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column("ai_runs", sa.Column("task_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_ai_runs_task_id_tasks"), "ai_runs", "tasks", ["task_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index(op.f("ix_ai_runs_task_id"), "ai_runs", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_runs_task_id"), table_name="ai_runs")
    op.drop_constraint(op.f("fk_ai_runs_task_id_tasks"), "ai_runs", type_="foreignkey")
    op.drop_column("ai_runs", "task_id")
    op.drop_constraint(op.f("fk_tasks_ai_assignee_id_users"), "tasks", type_="foreignkey")
    op.drop_column("tasks", "ai_assignee_id")
