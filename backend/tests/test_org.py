from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.conftest import UserClient, make_user
from wadima.db import session_scope
from wadima.models import Area, AreaMember, AuditLog, Organization

PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"\x00" * 40


def _body(org: dict, **changes) -> dict:
    keys = ("version", "platform_name", "legal_name", "short_mark", "description", "primary_color")
    return (
        {k: org[k] for k in keys} | {"secondary_color": org["secondary_color"], "timezone": org["timezone"]} | changes
    )


def test_default_identity_is_public_and_wadima(app):
    anon = TestClient(app)
    r = anon.get("/api/org/public")
    assert r.status_code == 200
    assert r.json() == {
        "platform_name": "WADIMA",
        "legal_name": "WADIMA INDUSTRIE",
        "short_mark": "W",
        "primary_color": "#6fd3ff",
        "secondary_color": "#ffd166",
        "logo_url": None,
        "timezone": "America/Bogota",
    }
    # La descripción y las políticas no se publican sin sesión.
    assert anon.get("/api/org").status_code == 401


def test_rename_persists_and_reaches_every_session(app, partners):
    c1, c2, _ = partners["clients"]
    org = c1.get("/api/org").json()
    assert c2.put("/api/org", json=_body(org, platform_name="VANTIA")).status_code == 403
    r = c1.put("/api/org", json=_body(org, platform_name="VANTIA", legal_name="VANTIA GROUP S.A.S.", short_mark="VG"))
    assert r.status_code == 200 and r.json()["version"] == org["version"] + 1
    # Concurrencia optimista: una edición basada en la versión anterior se rechaza.
    assert c1.put("/api/org", json=_body(org, platform_name="Otra")).status_code == 409
    # Sobrevive a una sesión nueva (y a un reinicio: está en la base de datos).
    fresh = UserClient(app, "socio3")
    assert fresh.me["org"]["platform_name"] == "VANTIA"
    assert fresh.me["org"]["legal_name"] == "VANTIA GROUP S.A.S."
    assert TestClient(app).get("/api/org/public").json()["short_mark"] == "VG"
    with session_scope() as db:
        assert (
            db.execute(select(func.count()).select_from(AuditLog).where(AuditLog.action == "org.updated")).scalar() == 1
        )
        # Los identificadores técnicos no cambian.
        assert db.execute(select(Organization.slug)).scalar_one() == "default"


def test_identity_validation(partners):
    c1 = partners["clients"][0]
    org = c1.get("/api/org").json()
    assert c1.put("/api/org", json=_body(org, primary_color="red")).status_code == 422
    assert c1.put("/api/org", json=_body(org, timezone="Marte/Olimpo")).status_code == 422
    assert c1.put("/api/org", json=_body(org, platform_name="")).status_code == 422
    ok = c1.put("/api/org", json=_body(org, timezone="Europe/Madrid"))
    assert ok.status_code == 200 and ok.json()["timezone"] == "Europe/Madrid"


def test_logo_upload_validation_and_serving(app, partners):
    c1, c2, _ = partners["clients"]
    assert c2.put("/api/org/logo", files={"file": ("l.png", PNG, "image/png")}).status_code == 403
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert c1.put("/api/org/logo", files={"file": ("l.svg", svg, "image/svg+xml")}).status_code == 422
    big = PNG + b"0" * (600 * 1024)
    assert c1.put("/api/org/logo", files={"file": ("l.png", big, "image/png")}).status_code == 422
    # El tipo se deduce de la firma, no de lo que declara el navegador.
    r = c1.put("/api/org/logo", files={"file": ("l.txt", PNG, "text/plain")})
    assert r.status_code == 200 and r.json()["logo_url"].startswith("/api/org/logo?v=")
    served = TestClient(app).get(r.json()["logo_url"])
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["content-security-policy"] == "default-src 'none'"
    assert c1.delete("/api/org/logo").json()["logo_url"] is None
    assert TestClient(app).get("/api/org/logo").status_code == 404


def test_setup_wizard_creates_predefined_areas_without_duplicates(app, partners):
    c1, c2, _ = partners["clients"]
    assert c1.me["setup_required"] is True and c2.me["setup_required"] is False
    assert c2.get("/api/org/setup").status_code == 403
    state = c1.get("/api/org/setup").json()
    assert {t["key"] for t in state["templates"]} == {"ops", "dev", "library", "ai", "meeting", "admin", "servers"}
    socio2 = next(u for u in state["users"] if u["username"] == "socio2")
    body = _body(state["org"], platform_name="VANTIA", legal_name="VANTIA GROUP") | {
        "areas": ["dev", "library", "meeting", "admin"],
        "users": [{"id": socio2["id"], "display_name": "Bruno Díaz", "email": "bruno@example.com"}],
    }
    r = c1.post("/api/org/setup", json=body)
    assert r.status_code == 200, r.text
    enabled = {t["key"] for t in r.json()["templates"] if t["enabled"]}
    assert enabled == {"ops", "dev", "library", "meeting", "admin"}  # operaciones siempre
    ops = next(t for t in r.json()["templates"] if t["key"] == "ops")
    assert ops["area_id"] == partners["general"]  # el área General existente se adopta, no se duplica
    assert UserClient(app, "socio1").me["setup_required"] is False
    assert UserClient(app, "socio2").me["user"]["display_name"] == "Bruno Díaz"
    with session_scope() as db:
        admin_area = db.execute(select(Area).where(Area.template_key == "admin")).scalar_one()
        roles = dict(
            db.execute(select(AreaMember.user_id, AreaMember.role).where(AreaMember.area_id == admin_area.id)).all()
        )
        # Permisos iniciales de la plantilla: miembros lectores en Administración, el administrador propietario.
        assert sorted(roles.values()) == ["owner", "viewer", "viewer"]
    # Repetir el asistente no crea áreas duplicadas.
    again = c1.get("/api/org/setup").json()
    assert c1.post("/api/org/setup", json=_body(again["org"]) | {"areas": ["dev", "ai"]}).status_code == 200
    with session_scope() as db:
        dev = db.execute(select(func.count()).select_from(Area).where(Area.template_key == "dev")).scalar()
        assert dev == 1
        assert db.execute(select(Area.enabled).where(Area.template_key == "library")).scalar_one() is False
        assert db.execute(select(Area.enabled).where(Area.template_key == "ai")).scalar_one() is True


def test_enable_disable_templates_and_custom_areas(partners):
    c1, c2, _ = partners["clients"]
    assert c2.post("/api/org/templates/dev/enable").status_code == 403
    templates = c1.post("/api/org/templates/dev/enable").json()
    dev = next(t for t in templates if t["key"] == "dev")
    assert dev["enabled"] and dev["area_id"]
    assert c1.post("/api/org/templates/ops/disable").status_code == 422
    assert c1.post("/api/org/templates/nada/enable").status_code == 404
    # Un socio puede entrar en el área habilitada; deshabilitada se conserva pero no admite entradas.
    assert c2.post(f"/api/areas/{dev['area_id']}/enter").status_code == 200
    c1.post("/api/org/templates/dev/disable")
    r = c2.post(f"/api/areas/{dev['area_id']}/enter")
    assert r.status_code == 409 and "deshabilitada" in r.json()["detail"]
    area = next(a for a in c1.get("/api/areas").json()["areas"] if a["id"] == dev["area_id"])
    assert area["enabled"] is False and area["template_key"] == "dev"
    # Áreas personalizadas: solo cuando hacen falta, en una de las siete salas.
    custom = c2.post("/api/areas", json={"name": "Clientes", "room_key": "ops"})
    assert custom.status_code == 200 and custom.json()["template_key"] is None


def test_office_has_seven_rooms_with_modules(partners):
    c1 = partners["clients"][0]
    rooms = {r["key"]: r for r in c1.get("/api/office/state").json()["rooms"]}
    assert set(rooms) == {"ops", "dev", "library", "ai", "meeting", "admin", "servers"}
    assert rooms["admin"]["module"] == "/time" and rooms["meeting"]["module"] == "/meetings"


def test_mic_allowed_only_for_own_origin(app):
    r = TestClient(app).get("/api/health")
    assert r.headers["permissions-policy"] == "camera=(), microphone=(self), geolocation=()"


def test_non_admin_member_sees_no_setup(app, partners):
    make_user("nueva")
    assert UserClient(app, "nueva").me["setup_required"] is False


def test_brand_reaches_generated_documents(partners):
    c1 = partners["clients"][0]
    org = c1.get("/api/org").json()
    c1.put("/api/org", json=_body(org, platform_name="VANTIA", legal_name="VANTIA GROUP"))
    p = c1.post("/api/projects", json={"area_id": partners["general"], "name": "Informe"}).json()
    c1.post(f"/api/projects/{p['id']}/tasks", json={"title": "Algo"})
    summary = c1.post(f"/api/projects/{p['id']}/summary").json()
    assert summary["markdown"].startswith("## VANTIA GROUP · Resumen de actividad")
    assert "America/Bogota" in summary["markdown"]
    sp = c1.post(
        "/api/studio/projects", json={"area_id": partners["general"], "name": "Sitio", "template": "static"}
    ).json()
    page = c1.get(f"/api/studio/projects/{sp['id']}/file", params={"path": "index.html"}).json()
    assert "Sitio estático de VANTIA" in page["content"]
    log = c1.get(f"/api/studio/projects/{sp['id']}/git/log").json()
    assert log[0]["message"] == "Proyecto creado desde VANTIA"


def test_office_state_shows_real_clock_and_integrations(partners):
    c1, c2, _ = partners["clients"]
    entry = c2.post("/api/time/clock-in", json={}).json()["entry"]
    state = c1.get("/api/office/state").json()
    people = {p["id"]: p for p in state["people"]}
    assert people[c2.id]["clock"]["clock_in_at"] == entry["clock_in_at"]
    assert people[c2.id]["clock"]["status"] == "open" and people[c1.id]["clock"] is None
    integrations = {i["key"]: i for i in state["integrations"]}
    assert set(integrations) == {"database", "drive", "claude_api", "claude_mcp", "jobs", "realtime"}
    # Sin credenciales nada aparece como conectado.
    assert integrations["drive"]["status"] == "off" and integrations["claude_api"]["status"] == "off"
    assert integrations["claude_mcp"]["status"] == "off" and state["org"]["platform_name"] == "WADIMA"
