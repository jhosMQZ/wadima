"""Infraestructura de pruebas: PostgreSQL real (base wadima_test) migrada con Alembic."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

# El módulo se importa dos veces (como conftest y como tests.conftest): ambas copias comparten el directorio.
if "WADIMA_TEST_SESSION_DIR" not in os.environ:
    os.environ["WADIMA_TEST_SESSION_DIR"] = tempfile.mkdtemp(prefix="wadima-test-")
_tmp = Path(os.environ["WADIMA_TEST_SESSION_DIR"])
os.environ["WADIMA_ENV"] = "test"
os.environ["WADIMA_DATA_DIR"] = str(_tmp)
os.environ["WADIMA_MASTER_KEY"] = "test-master-key-not-secret"
os.environ["WADIMA_EMBEDDED_WORKERS"] = "false"
# Las pruebas usan el cliente HTTP local: no deben heredar la publicación real del servidor (.env con HTTPS,
# Tailscale o red local), que cambiaría las cookies, los orígenes y la interfaz de escucha.
os.environ["WADIMA_COOKIE_SECURE"] = "false"
os.environ["WADIMA_PUBLIC_ORIGIN"] = "http://127.0.0.1:8000"
os.environ["WADIMA_PREVIEW_PUBLIC_ORIGIN"] = "http://127.0.0.1:8001"
os.environ["WADIMA_ALLOWED_ORIGINS"] = (
    '["http://127.0.0.1:8000","http://localhost:8000","http://127.0.0.1:5173","http://localhost:5173"]'
)
os.environ["WADIMA_BIND_HOST"] = "127.0.0.1"
os.environ["WADIMA_TLS_CERT_FILE"] = ""
os.environ["WADIMA_TLS_KEY_FILE"] = ""

from wadima.config import get_settings  # noqa: E402

_settings = get_settings()
if not _settings.test_database_url:
    raise RuntimeError("Defina WADIMA_TEST_DATABASE_URL (python manage.py db init lo escribe en .env)")
# Rol propietario para crear el esquema y limpiar; la aplicación usa el rol sin propiedad, como en producción.
OWNER_URL = _settings.test_database_url
os.environ["WADIMA_DATABASE_URL"] = _settings.test_app_database_url or OWNER_URL
os.environ["WADIMA_MIGRATION_DATABASE_URL"] = OWNER_URL
get_settings.cache_clear()

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from wadima.db import init_engine, session_scope  # noqa: E402
from wadima.models import DEFAULT_ORG_ID, Area, AreaMember, Base, User  # noqa: E402
from wadima.security.crypto import hash_password  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "Prueba-Segura-123"


@pytest.fixture(scope="session", autouse=True)
def database():
    """Motor con el rol propietario (para preparar y limpiar). La aplicación usa su propio motor."""
    engine = create_engine(OWNER_URL, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["url"] = OWNER_URL
    command.upgrade(cfg, "head")
    app_engine = init_engine(get_settings().database_url)
    yield engine
    app_engine.dispose()
    engine.dispose()
    from wadima.studio import runner

    runner.stop_all()
    shutil.rmtree(_tmp, onexc=_force_remove)  # datos temporales de la sesión (blobs, proyectos de Studio)


def _force_remove(func, path, _exc) -> None:
    """Los objetos de Git son de solo lectura en Windows y los procesos recién terminados pueden tardar en
    liberar sus archivos: se quita el atributo y se reintenta unos instantes."""
    for _ in range(20):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.25)


IMMUTABLE_TABLES = ("audit_log", "area_visits", "time_entries", "time_events")


def wipe(conn) -> None:
    """Deja la base como recién migrada: tablas vacías y solo la organización por defecto (la crea la migración)."""
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    for t in IMMUTABLE_TABLES:
        conn.execute(text(f"ALTER TABLE {t} DISABLE TRIGGER USER"))
    conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    conn.execute(
        text(
            "INSERT INTO organizations (id, slug, platform_name, legal_name, short_mark, description, "
            "primary_color, secondary_color, timezone, settings, version) VALUES (:id, 'default', 'WADIMA', "
            "'WADIMA INDUSTRIE', 'W', '', '#6fd3ff', '#ffd166', 'America/Bogota', '{}'::jsonb, 1)"
        ),
        {"id": str(DEFAULT_ORG_ID)},
    )
    for t in IMMUTABLE_TABLES:
        conn.execute(text(f"ALTER TABLE {t} ENABLE TRIGGER USER"))


@pytest.fixture(autouse=True)
def clean_tables(database):
    yield
    with database.begin() as conn:
        wipe(conn)


@pytest.fixture
def app():
    from wadima.main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def server(app):
    """Cliente con lifespan activo (necesario para entregar eventos de WebSocket)."""
    with TestClient(app) as c:
        yield c


class UserClient:
    """Cliente HTTP autenticado como un usuario concreto (cookies propias + cabecera CSRF)."""

    def __init__(self, app, username: str, password: str = PASSWORD):
        self.http = TestClient(app)
        self.username = username
        r = self.http.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        self.me = r.json()
        self.csrf = self.me["csrf_token"]
        self.id = self.me["user"]["id"]

    def _h(self, kw):
        headers = kw.pop("headers", {}) or {}
        headers.setdefault("X-CSRF-Token", self.csrf)
        return headers

    def get(self, url, **kw):
        return self.http.get(url, **kw)

    def post(self, url, **kw):
        return self.http.post(url, headers=self._h(kw), **kw)

    def put(self, url, **kw):
        return self.http.put(url, headers=self._h(kw), **kw)

    def patch(self, url, **kw):
        return self.http.patch(url, headers=self._h(kw), **kw)

    def delete(self, url, **kw):
        return self.http.delete(url, headers=self._h(kw), **kw)


def make_user(username: str, role: str = "member", must_change: bool = False, display: str | None = None) -> str:
    with session_scope() as db:
        u = User(
            username=username,
            display_name=display or username.title(),
            role=role,
            password_hash=hash_password(PASSWORD),
            must_change_password=must_change,
        )
        db.add(u)
        db.flush()
        return str(u.id)


@pytest.fixture
def partners(app):
    """Los tres socios (socio1 admin) y el área General con los tres como propietarios."""
    ids = [make_user("socio1", "admin"), make_user("socio2"), make_user("socio3")]
    with session_scope() as db:
        area = Area(slug="general", name="General", room_key="ops")
        db.add(area)
        db.flush()
        for uid in ids:
            db.add(AreaMember(area_id=area.id, user_id=uid, role="owner"))
        area_id = str(area.id)
    clients = [UserClient(app, f"socio{i}") for i in (1, 2, 3)]
    return {"clients": clients, "general": area_id}
