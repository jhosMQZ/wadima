"""app role privileges

La aplicación se conecta con el rol wadima_app, que no es propietario de las tablas: no puede desactivar los
triggers de inmutabilidad (ALTER TABLE ... DISABLE TRIGGER) ni modificar la bitácora o borrar registros horarios.
Las migraciones las aplica el rol propietario. Si wadima_app no existe (instalaciones antiguas), no hace nada.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25 05:00:00
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = "wadima_app"

GRANTS = [
    f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    f"REVOKE UPDATE, DELETE ON audit_log FROM {APP_ROLE}",
    f"REVOKE DELETE ON area_visits FROM {APP_ROLE}",
    f"REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM {APP_ROLE}",
    # Tablas y secuencias que creen migraciones futuras (las crea este mismo rol propietario).
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}",
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}",
]

REVOKES = [
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {APP_ROLE}",
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {APP_ROLE}",
    f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}",
    f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}",
    f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}",
]


def _run_if_role_exists(statements: list[str]) -> None:
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
    _run_if_role_exists(GRANTS)


def downgrade() -> None:
    _run_if_role_exists(REVOKES)
