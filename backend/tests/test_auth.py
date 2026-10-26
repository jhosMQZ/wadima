from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from tests.conftest import PASSWORD, UserClient, make_user
from wadima.db import session_scope
from wadima.models import AuthSession


def test_health(server):
    r = server.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] is True
    assert r.headers["x-content-type-options"] == "nosniff"


def test_login_sets_httponly_cookie_and_hides_secrets(app):
    make_user("ana")
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "ANA ", "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "wadima_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax")
    body = r.json()
    assert "password_hash" not in str(body)
    assert body["user"]["username"] == "ana"
    # El token de la cookie nunca se guarda en claro.
    token = c.cookies.get("wadima_session")
    with session_scope() as db:
        hashes = list(db.execute(select(AuthSession.token_hash)).scalars())
    assert token not in hashes and len(hashes[0]) == 64


def test_wrong_password_and_rate_limit(app):
    make_user("bob")
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "bob", "password": "malo"}).status_code == 401
    # Bloqueado incluso con la contraseña correcta.
    assert c.post("/api/auth/login", json={"username": "bob", "password": PASSWORD}).status_code == 429


def test_unknown_user_same_error(app):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "nadie", "password": "x"})
    assert r.status_code == 401 and r.json()["detail"] == "Usuario o contraseña incorrectos"


def test_csrf_and_origin_required(app):
    make_user("carla")
    u = UserClient(app, "carla")
    # Sin cabecera CSRF -> 403
    r = u.http.put("/api/me/status", json={"declared": "working"})
    assert r.status_code == 403
    # Origen no permitido -> 403
    r = u.put(
        "/api/me/status",
        json={"declared": "working"},
        headers={"Origin": "https://evil.example", "X-CSRF-Token": u.csrf},
    )
    assert r.status_code == 403
    assert u.put("/api/me/status", json={"declared": "working"}).status_code == 200


def test_must_change_password_flow(app):
    make_user("dani", must_change=True)
    u = UserClient(app, "dani")
    assert u.me["user"]["must_change_password"] is True
    assert u.get("/api/areas").status_code == 403
    weak = u.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "corta"})
    assert weak.status_code == 422
    ok = u.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "Nueva-Clave-456"})
    assert ok.status_code == 200
    assert u.get("/api/areas").status_code == 200
    assert UserClient(app, "dani", "Nueva-Clave-456").me["user"]["must_change_password"] is False


def test_logout_revokes_session(app):
    make_user("eva")
    u = UserClient(app, "eva")
    assert u.get("/api/auth/me").status_code == 200
    assert u.post("/api/auth/logout").status_code == 200
    # Reutilizar el token antiguo no funciona aunque se reenvíe la cookie.
    assert u.get("/api/auth/me").status_code == 401


def test_idle_session_expires(app):
    make_user("fer")
    u = UserClient(app, "fer")
    with session_scope() as db:
        db.execute(text("UPDATE auth_sessions SET last_seen_at = now() - interval '2 days'"))
    assert u.get("/api/auth/me").status_code == 401


def test_revoke_other_session(app):
    make_user("gabi")
    a = UserClient(app, "gabi")
    b = UserClient(app, "gabi")
    sessions = a.get("/api/auth/sessions").json()
    other = next(s for s in sessions if not s["current"])
    assert a.delete(f"/api/auth/sessions/{other['id']}").status_code == 200
    assert b.get("/api/auth/me").status_code == 401
    assert a.get("/api/auth/me").status_code == 200


def test_admin_user_management(partners):
    admin, member, _ = partners["clients"]
    r = member.post("/api/users", json={"username": "nuevo", "display_name": "Nuevo"})
    assert r.status_code == 403
    r = admin.post("/api/users", json={"username": "nuevo", "display_name": "Nuevo"})
    assert r.status_code == 200 and len(r.json()["temporary_password"]) >= 12
    uid = r.json()["user"]["id"]
    assert admin.patch(f"/api/users/{admin.id}", json={"role": "member"}).status_code == 400
    assert admin.patch(f"/api/users/{uid}", json={"is_active": False}).status_code == 200
