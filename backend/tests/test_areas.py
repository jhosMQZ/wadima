from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import UserClient, make_user
from wadima.db import session_scope


def test_three_partners_independent_sessions(partners):
    c1, c2, c3 = partners["clients"]
    ids = {c1.me["session"]["id"], c2.me["session"]["id"], c3.me["session"]["id"]}
    assert len(ids) == 3
    users = c2.get("/api/users").json()
    assert {u["username"] for u in users} == {"socio1", "socio2", "socio3"}


def test_area_crud_and_permissions(app, partners):
    c1, c2, _ = partners["clients"]
    make_user("externo")
    ext = UserClient(app, "externo")
    # Una sala que ya no existe en la oficina compacta se rechaza.
    assert c2.post("/api/areas", json={"name": "Finanzas", "room_key": "finance"}).status_code == 422
    r = c2.post("/api/areas", json={"name": "Finanzas e Inversiones", "room_key": "admin"})
    assert r.status_code == 200
    area = r.json()
    assert area["slug"] == "finanzas-e-inversiones" and area["my_role"] == "owner"
    # Un usuario sin membresía no ve el área (404, no 403, para no revelar su existencia)
    assert ext.get(f"/api/areas/{area['id']}").status_code == 404
    assert area["id"] not in [a["id"] for a in ext.get("/api/areas").json()["areas"]]
    # El admin tiene acceso implícito
    assert c1.get(f"/api/areas/{area['id']}").status_code == 200
    # Concurrencia optimista
    ok = c2.patch(f"/api/areas/{area['id']}", json={"version": 1, "description": "Cartera"})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = c2.patch(f"/api/areas/{area['id']}", json={"version": 1, "description": "otra"})
    assert stale.status_code == 409
    # Borrado seguro: requiere confirmar el identificador
    assert c2.post(f"/api/areas/{area['id']}/archive", json={"confirm_slug": "x"}).status_code == 422
    assert (
        c2.post(f"/api/areas/{area['id']}/archive", json={"confirm_slug": "finanzas-e-inversiones"}).status_code == 200
    )
    assert area["id"] not in [a["id"] for a in c2.get("/api/areas").json()["areas"]]
    assert c1.post(f"/api/areas/{area['id']}/restore").status_code == 200


def test_invitation_flow_with_expiry_revocation_and_approval(app, partners):
    c1, _, _ = partners["clients"]
    make_user("invitado")
    make_user("otro")
    inv_user = UserClient(app, "invitado")
    other = UserClient(app, "otro")
    area = c1.post("/api/areas", json={"name": "Ciberseguridad", "room_key": "servers"}).json()

    r = c1.post(f"/api/areas/{area['id']}/invitations", json={"role": "viewer", "max_uses": 1})
    code = r.json()["code"]
    assert code.startswith("WAD-")
    # Códigos con formato libre (minúsculas, sin guiones) se aceptan
    red = inv_user.post("/api/invitations/redeem", json={"code": code.lower().replace("-", " ")})
    assert red.status_code == 200 and red.json()["status"] == "auto"
    assert inv_user.get(f"/api/areas/{area['id']}").json()["my_role"] == "viewer"
    # Agotado
    assert other.post("/api/invitations/redeem", json={"code": code}).status_code == 400

    # Con aprobación
    r = c1.post(f"/api/areas/{area['id']}/invitations", json={"role": "editor", "requires_approval": True})
    code2, inv_id = r.json()["code"], r.json()["invitation"]["id"]
    red = other.post("/api/invitations/redeem", json={"code": code2})
    assert red.json()["status"] == "pending"
    assert other.get(f"/api/areas/{area['id']}").status_code == 404
    reqs = c1.get(f"/api/areas/{area['id']}/requests").json()
    pending = next(q for q in reqs if q["status"] == "pending")
    assert c1.post(f"/api/access-requests/{pending['id']}/decide", json={"approve": True}).status_code == 200
    assert other.get(f"/api/areas/{area['id']}").json()["my_role"] == "editor"
    # Un viewer no puede crear invitaciones
    assert inv_user.post(f"/api/areas/{area['id']}/invitations", json={}).status_code == 403

    # Revocación y caducidad
    r = c1.post(f"/api/areas/{area['id']}/invitations", json={"max_uses": 5}).json()
    assert c1.post(f"/api/invitations/{r['invitation']['id']}/revoke").json()["state"] == "revoked"
    make_user("tercero")
    t = UserClient(app, "tercero")
    assert t.post("/api/invitations/redeem", json={"code": r["code"]}).status_code == 400
    r = c1.post(f"/api/areas/{area['id']}/invitations", json={"max_uses": 5}).json()
    with session_scope() as db:
        db.execute(
            text("UPDATE invitations SET expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": r["invitation"]["id"]},
        )
    assert t.post("/api/invitations/redeem", json={"code": r["code"]}).status_code == 400
    assert inv_id


def test_enter_leave_records_server_time_and_audit(partners):
    c1, c2, _ = partners["clients"]
    gid = partners["general"]
    r = c2.post(f"/api/areas/{gid}/enter")
    assert r.status_code == 200
    entered = r.json()["visit"]["entered_at"]
    assert c2.get("/api/auth/me").json()["active_area"]["id"] == gid
    left = c2.post("/api/areas/leave").json()["visit"]
    assert left["left_at"] >= entered
    visits = c1.get(f"/api/areas/{gid}/visits").json()
    assert visits[0]["entry_audit_id"] and visits[0]["exit_audit_id"]
    audit = c1.get("/api/admin/audit?action=area.enter").json()
    assert audit and audit[0]["target_id"] == visits[0]["id"]
    assert c1.get("/api/admin/audit/verify").json()["ok"] is True


def test_logout_closes_open_visit(partners):
    _, c2, _ = partners["clients"]
    gid = partners["general"]
    c2.post(f"/api/areas/{gid}/enter")
    c2.post("/api/auth/logout")
    with session_scope() as db:
        row = db.execute(text("SELECT end_reason, left_at FROM area_visits")).one()
    assert row.end_reason == "logout" and row.left_at is not None


def test_time_records_are_immutable(partners):
    _, c2, _ = partners["clients"]
    gid = partners["general"]
    c2.post(f"/api/areas/{gid}/enter")
    c2.post("/api/areas/leave")
    for sql in (
        "UPDATE area_visits SET entered_at = entered_at - interval '1 hour'",
        "UPDATE area_visits SET left_at = left_at + interval '1 hour'",
        "DELETE FROM area_visits",
        "UPDATE audit_log SET action = 'x'",
        "DELETE FROM audit_log",
    ):
        with pytest.raises(DBAPIError), session_scope() as db:
            db.execute(text(sql))


def test_app_role_cannot_bypass_immutability(partners):
    """La aplicación se conecta sin ser propietaria de las tablas: no puede desactivar los triggers."""
    from wadima.config import get_settings

    s = get_settings()
    if not s.migration_database_url or s.migration_database_url == s.database_url:
        pytest.skip("Sin rol de aplicación separado (WADIMA_TEST_APP_DATABASE_URL)")
    partners["clients"][1].post(f"/api/areas/{partners['general']}/enter")
    for sql in (
        "ALTER TABLE audit_log DISABLE TRIGGER USER",
        "ALTER TABLE area_visits DISABLE TRIGGER USER",
        "TRUNCATE audit_log",
        "DROP TABLE audit_log",
    ):
        denied = "InsufficientPrivilege|permission denied|permiso denegado|must be owner|debe ser dueño"
        with pytest.raises(DBAPIError, match=denied), session_scope() as db:
            db.execute(text(sql))


def test_audit_chain_detects_tampering(partners, database):
    c1, c2, _ = partners["clients"]
    c2.post(f"/api/areas/{partners['general']}/enter")
    assert c1.get("/api/admin/audit/verify").json()["ok"] is True
    # Manipulación directa (saltándose el trigger como haría un atacante con privilegios de dueño)
    with database.begin() as conn:
        conn.execute(text("ALTER TABLE audit_log DISABLE TRIGGER USER"))
        conn.execute(text("UPDATE audit_log SET data = '{\"forged\": true}' WHERE id = 2"))
        conn.execute(text("ALTER TABLE audit_log ENABLE TRIGGER USER"))
    result = c1.get("/api/admin/audit/verify").json()
    assert result["ok"] is False and result["broken_at"] == 2


def test_member_removal_and_last_owner(partners):
    c1, c2, c3 = partners["clients"]
    area = c2.post("/api/areas", json={"name": "Proyecto X"}).json()
    assert c2.delete(f"/api/areas/{area['id']}/members/{c2.id}").status_code == 400  # último propietario
    assert c2.put(f"/api/areas/{area['id']}/members/{c3.id}", json={"role": "viewer"}).status_code == 200
    assert c3.get(f"/api/areas/{area['id']}").status_code == 200
    c3.post(f"/api/areas/{area['id']}/enter")
    assert c2.delete(f"/api/areas/{area['id']}/members/{c3.id}").status_code == 200
    assert c3.get(f"/api/areas/{area['id']}").status_code == 404
    assert c3.get("/api/auth/me").json()["active_area"] is None
    assert c1
