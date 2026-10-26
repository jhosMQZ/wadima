"""Preparación de la base de datos de pruebas extremo a extremo (solo bases *_e2e).

Datos de demostración claramente separados de los reales: se usan exclusivamente en wadima_e2e.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from wadima.config import get_settings

E2E_PASSWORD = os.environ.get("WADIMA_E2E_PASSWORD", "E2e-Prueba-2026!")


def reset() -> None:
    s = get_settings()
    url = os.environ.get("WADIMA_MIGRATION_DATABASE_URL") or s.migration_database_url or s.database_url
    if not make_url(url).database or not make_url(url).database.endswith("_e2e"):
        sys.exit("Por seguridad, reset solo se permite sobre bases de datos *_e2e")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "migrations"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")

    from wadima.cli import AVATARS
    from wadima.db import init_engine, session_scope
    from wadima.models import Area, AreaMember, User
    from wadima.security.crypto import hash_password

    init_engine(url)
    with session_scope() as db:
        users = []
        for i, (username, name, role) in enumerate(
            [("socio1", "Ana (E2E)", "admin"), ("socio2", "Bruno (E2E)", "member"), ("socio3", "Carla (E2E)", "member")]
        ):
            u = User(
                username=username,
                display_name=name,
                role=role,
                password_hash=hash_password(E2E_PASSWORD),
                must_change_password=False,
                avatar=AVATARS[i],
            )
            db.add(u)
            users.append(u)
        db.add(
            User(
                username="nuevo",
                display_name="Nuevo (E2E)",
                role="member",
                password_hash=hash_password(E2E_PASSWORD),
                must_change_password=True,
            )
        )
        db.flush()
        area = Area(slug="general", name="General", room_key="ops", description="Área de pruebas E2E")
        db.add(area)
        db.flush()
        for u in users:
            db.add(AreaMember(area_id=area.id, user_id=u.id, role="owner"))
        db.flush()
        # Organización ya configurada con las siete áreas predefinidas (el asistente se prueba en /setup).
        from wadima import org as orgsvc
        from wadima.security.auth import now_utc

        for key in orgsvc.AREA_TEMPLATES:
            tpl_area = orgsvc.ensure_template_area(db, key, users[0])
            orgsvc.add_default_members(db, tpl_area, key, users)
        orgsvc.get_org(db).setup_completed_at = now_utc()
    print("Base E2E preparada")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reset":
        reset()
    else:
        sys.exit("uso: python -m wadima.e2e reset")
