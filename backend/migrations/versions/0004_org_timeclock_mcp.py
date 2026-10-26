"""organización configurable, control horario, plantillas de áreas y tokens MCP

- organizations: identidad (nombre comercial, logotipo, colores), zona horaria y políticas. Una fila por
  defecto con los valores de WADIMA; usuarios y áreas pasan a referenciarla.
- areas: plantilla de origen, icono y habilitación. Las salas de la oficina se reducen a siete; las áreas
  asignadas a salas que desaparecen se reubican (finanzas y proyectos → operaciones, seguridad → administración).
- time_entries / time_events: jornadas con hora del servidor, una sola abierta por persona, sin borrado; el
  historial de eventos es de solo inserción.
- mcp_tokens: tokens personales (solo hash) para clientes MCP oficiales de Claude.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25 13:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "wadima_app"
DEFAULT_ORG = "00000000-0000-4000-8000-000000000001"

TIME_GUARD = """CREATE OR REPLACE FUNCTION wadima_time_entry_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Las jornadas registradas no se pueden borrar' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
     OR NEW.clock_in_at IS DISTINCT FROM OLD.clock_in_at OR NEW.in_source IS DISTINCT FROM OLD.in_source
     OR NEW.in_session_id IS DISTINCT FROM OLD.in_session_id OR NEW.in_ip IS DISTINCT FROM OLD.in_ip
     OR NEW.in_note IS DISTINCT FROM OLD.in_note OR NEW.in_audit_id IS DISTINCT FROM OLD.in_audit_id THEN
    RAISE EXCEPTION 'Los datos de entrada de una jornada son inmutables' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.clock_out_at IS NOT NULL AND (
       NEW.clock_out_at IS DISTINCT FROM OLD.clock_out_at OR NEW.close_kind IS DISTINCT FROM OLD.close_kind
       OR NEW.out_source IS DISTINCT FROM OLD.out_source OR NEW.out_ip IS DISTINCT FROM OLD.out_ip
       OR NEW.out_note IS DISTINCT FROM OLD.out_note OR NEW.close_reason IS DISTINCT FROM OLD.close_reason
       OR NEW.closed_by IS DISTINCT FROM OLD.closed_by OR NEW.out_audit_id IS DISTINCT FROM OLD.out_audit_id) THEN
    RAISE EXCEPTION 'La salida ya fue registrada y no puede modificarse' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.clock_out_at IS NULL AND NEW.approval_status IS DISTINCT FROM OLD.approval_status THEN
    RAISE EXCEPTION 'Solo se aprueban jornadas cerradas' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$"""


def _if_app_role(statements: list[str]) -> None:
    body = "\n".join(f"    EXECUTE '{stmt}';" for stmt in statements)
    op.execute(
        f"""DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
{body}
  END IF;
END $$"""  # noqa: S608 - sentencias constantes, sin datos externos
    )


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("platform_name", sa.String(length=60), nullable=False),
        sa.Column("legal_name", sa.String(length=160), nullable=False),
        sa.Column("short_mark", sa.String(length=4), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("primary_color", sa.String(length=7), nullable=False),
        sa.Column("secondary_color", sa.String(length=7), nullable=False),
        sa.Column("logo", sa.LargeBinary(), nullable=True),
        sa.Column("logo_mime", sa.String(length=40), nullable=True),
        sa.Column("logo_sha256", sa.String(length=64), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("setup_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("slug", name=op.f("uq_organizations_slug")),
    )
    op.execute(
        f"""INSERT INTO organizations (id, slug, platform_name, legal_name, short_mark, description, primary_color,
                secondary_color, timezone, settings, version)
            VALUES ('{DEFAULT_ORG}', 'default', 'WADIMA', 'WADIMA INDUSTRIE', 'W', '', '#6fd3ff', '#ffd166',
                'America/Bogota', '{{}}'::jsonb, 1)"""  # noqa: S608 - constantes
    )

    for table in ("users", "areas"):
        op.add_column(table, sa.Column("organization_id", sa.UUID(), nullable=True))
        op.execute(f"UPDATE {table} SET organization_id = '{DEFAULT_ORG}'")  # noqa: S608 - constantes
        op.alter_column(table, "organization_id", nullable=False)
        op.create_foreign_key(
            op.f(f"fk_{table}_organization_id_organizations"),
            table,
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.add_column("areas", sa.Column("template_key", sa.String(length=30), nullable=True))
    op.add_column("areas", sa.Column("icon", sa.String(length=16), server_default="", nullable=False))
    op.add_column("areas", sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.create_index(
        "uq_areas_template_active",
        "areas",
        ["organization_id", "template_key"],
        unique=True,
        postgresql_where=sa.text("template_key IS NOT NULL AND archived_at IS NULL"),
    )
    op.alter_column("areas", "room_key", server_default=None)
    for old, new in (("finance", "ops"), ("projects", "ops"), ("security", "admin")):
        op.execute(f"UPDATE areas SET room_key = '{new}' WHERE room_key = '{old}'")  # noqa: S608 - constantes
        op.execute(f"UPDATE user_status SET room_key = '{new}' WHERE room_key = '{old}'")  # noqa: S608

    op.create_table(
        "time_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "clock_in_at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False
        ),
        sa.Column("clock_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_kind", sa.String(length=10), nullable=True),
        sa.Column("in_source", sa.String(length=20), nullable=False),
        sa.Column("out_source", sa.String(length=20), nullable=True),
        sa.Column("in_session_id", sa.UUID(), nullable=True),
        sa.Column("out_session_id", sa.UUID(), nullable=True),
        sa.Column("in_ip", sa.String(length=64), nullable=True),
        sa.Column("out_ip", sa.String(length=64), nullable=True),
        sa.Column("in_note", sa.String(length=300), nullable=False),
        sa.Column("out_note", sa.String(length=300), nullable=False),
        sa.Column("close_reason", sa.String(length=300), nullable=False),
        sa.Column("closed_by", sa.UUID(), nullable=True),
        sa.Column("pending_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_reason", sa.String(length=40), nullable=True),
        sa.Column("approval_status", sa.String(length=10), nullable=False),
        sa.Column("approved_by", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_note", sa.String(length=300), nullable=False),
        sa.Column("in_audit_id", sa.BigInteger(), nullable=True),
        sa.Column("out_audit_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("(clock_out_at IS NULL) = (close_kind IS NULL)", name=op.f("ck_time_entries_closed_kind")),
        sa.CheckConstraint(
            "approval_status IN ('pending','approved','rejected')", name=op.f("ck_time_entries_approval")
        ),
        sa.CheckConstraint("clock_out_at IS NULL OR clock_out_at >= clock_in_at", name=op.f("ck_time_entries_order")),
        sa.CheckConstraint(
            "close_kind IS NULL OR close_kind IN ('manual','auto','declared','admin')",
            name=op.f("ck_time_entries_close_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"], name=op.f("fk_time_entries_approved_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["closed_by"], ["users.id"], name=op.f("fk_time_entries_closed_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["in_session_id"],
            ["auth_sessions.id"],
            name=op.f("fk_time_entries_in_session_id_auth_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_time_entries_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["out_session_id"],
            ["auth_sessions.id"],
            name=op.f("fk_time_entries_out_session_id_auth_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_time_entries_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_entries")),
    )
    op.create_index("ix_time_entries_user_in", "time_entries", ["user_id", "clock_in_at"], unique=False)
    op.create_index(
        "uq_time_entries_open",
        "time_entries",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("clock_out_at IS NULL"),
    )
    op.create_table(
        "time_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("clock_timestamp()"), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("audit_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('clock_in','clock_out','auto_close','declared_close','admin_close','flag_pending',"
            "'approve','reject','duplicate_blocked')",
            name=op.f("ck_time_events_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["time_entries.id"], name=op.f("fk_time_events_entry_id_time_entries"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_events")),
    )
    op.create_index("ix_time_events_user_at", "time_events", ["user_id", "at"], unique=False)
    op.execute(TIME_GUARD)
    op.execute(
        """CREATE TRIGGER time_entries_guard BEFORE UPDATE OR DELETE ON time_entries
           FOR EACH ROW EXECUTE FUNCTION wadima_time_entry_guard()"""
    )
    op.execute(
        """CREATE TRIGGER time_entries_no_truncate BEFORE TRUNCATE ON time_entries
           FOR EACH STATEMENT EXECUTE FUNCTION wadima_forbid_change()"""
    )
    op.execute(
        """CREATE TRIGGER time_events_immutable BEFORE UPDATE OR DELETE ON time_events
           FOR EACH ROW EXECUTE FUNCTION wadima_forbid_change()"""
    )
    op.execute(
        """CREATE TRIGGER time_events_no_truncate BEFORE TRUNCATE ON time_events
           FOR EACH STATEMENT EXECUTE FUNCTION wadima_forbid_change()"""
    )

    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_hint", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_client", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_mcp_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_mcp_tokens_token_hash")),
    )
    op.create_index(op.f("ix_mcp_tokens_user_id"), "mcp_tokens", ["user_id"], unique=False)

    # Las tablas nuevas reciben los privilegios por defecto (migración 0003); se retiran los de borrado.
    _if_app_role(
        [
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON organizations, time_entries, time_events, mcp_tokens TO {APP_ROLE}",
            f"GRANT USAGE, SELECT ON SEQUENCE time_events_id_seq TO {APP_ROLE}",
            f"REVOKE DELETE ON organizations FROM {APP_ROLE}",
            f"REVOKE DELETE ON time_entries FROM {APP_ROLE}",
            f"REVOKE UPDATE, DELETE ON time_events FROM {APP_ROLE}",
        ]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_mcp_tokens_user_id"), table_name="mcp_tokens")
    op.drop_table("mcp_tokens")
    op.execute("DROP TRIGGER IF EXISTS time_events_no_truncate ON time_events")
    op.execute("DROP TRIGGER IF EXISTS time_events_immutable ON time_events")
    op.execute("DROP TRIGGER IF EXISTS time_entries_no_truncate ON time_entries")
    op.execute("DROP TRIGGER IF EXISTS time_entries_guard ON time_entries")
    op.drop_table("time_events")
    op.drop_table("time_entries")
    op.execute("DROP FUNCTION IF EXISTS wadima_time_entry_guard()")
    op.drop_index("uq_areas_template_active", table_name="areas")
    op.drop_column("areas", "enabled")
    op.drop_column("areas", "icon")
    op.drop_column("areas", "template_key")
    for table in ("areas", "users"):
        op.drop_constraint(op.f(f"fk_{table}_organization_id_organizations"), table, type_="foreignkey")
        op.drop_column(table, "organization_id")
    op.drop_table("organizations")
