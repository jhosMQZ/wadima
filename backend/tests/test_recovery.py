from __future__ import annotations

import io
import json
import threading

from sqlalchemy import func, select

from tests.conftest import wipe
from tests.test_drive import connect, drain, fake  # noqa: F401 - fixture del simulador de Drive
from wadima.backup import missing_blobs, rebuild_blobs_from_drive, restore_logical
from wadima.db import session_scope
from wadima.drive.providers import backup_payload
from wadima.library.storage import blob_path
from wadima.models import DEFAULT_ORG_ID, DocumentVersion, Note, Organization, Project, Task, TimeEntry, TimeEvent, User
from wadima.services import jobs

PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _populate(partners):
    c1, c2, _ = partners["clients"]
    g = partners["general"]
    p = c1.post("/api/projects", json={"area_id": g, "name": "Recuperable"}).json()
    parent = c1.post(f"/api/projects/{p['id']}/tasks", json={"title": "Padre"}).json()
    c1.post(f"/api/projects/{p['id']}/tasks", json={"title": "Hija", "parent_id": parent["id"]})
    c2.post(
        "/api/notes", json={"area_id": g, "project_id": p["id"], "title": "Plan", "content": "Contenido del plan\n"}
    )
    r = c1.post(
        "/api/library/upload",
        data={"area_id": g},
        files={"file": ("datos.md", io.BytesIO(b"# Datos\n\nPresupuesto: 100 EUR\n"))},
    )
    jobs.drain(["document.process"])
    return r.json()


def test_logical_backup_restores_into_empty_database(partners, database):
    _populate(partners)
    c1, c2, _ = partners["clients"]
    org = c1.get("/api/org").json()
    body = {k: org[k] for k in ("version", "platform_name", "legal_name", "short_mark", "description")}
    body |= {"primary_color": "#123456", "secondary_color": "#abcdef", "timezone": "America/Bogota"}
    assert c1.put("/api/org", json=body | {"platform_name": "VANTIA", "legal_name": "VANTIA GROUP"}).status_code == 200
    png = PNG_SIGNATURE + b"0" * 64
    assert c1.put("/api/org/logo", files={"file": ("logo.png", png, "image/png")}).status_code == 200
    entry = c2.post("/api/time/clock-in", json={}).json()["entry"]
    c2.post("/api/time/clock-out", json={"entry_id": entry["id"]})
    c2.post("/api/time/clock-in", json={})  # una jornada abierta también debe sobrevivir
    with session_scope() as db:
        payload = json.loads(backup_payload(db))
    assert "password_hash" not in json.dumps(payload)
    assert "token_hash" not in json.dumps(payload) and "mcp_tokens" not in payload["tables"]
    with database.begin() as conn:  # simula una base nueva tras perder el equipo
        wipe(conn)
    with session_scope() as db:
        result = restore_logical(db, payload)
    assert set(result["temporary_passwords"]) == {"socio1", "socio2", "socio3"}
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(Project)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(Task)).scalar_one() == 2
        assert db.execute(select(Note.content)).scalar_one() == "Contenido del plan\n"
        assert db.execute(select(func.count()).select_from(DocumentVersion)).scalar_one() == 1
        assert all(u.must_change_password for u in db.execute(select(User)).scalars())
        restored_org = db.get(Organization, DEFAULT_ORG_ID)
        assert (restored_org.platform_name, restored_org.legal_name) == ("VANTIA", "VANTIA GROUP")
        assert restored_org.logo == png
        entries = db.execute(select(TimeEntry).order_by(TimeEntry.clock_in_at)).scalars().all()
        assert len(entries) == 2 and entries[0].clock_out_at is not None and entries[1].clock_out_at is None
        assert [e.kind for e in db.execute(select(TimeEvent).order_by(TimeEvent.id)).scalars()] == [
            "clock_in",
            "clock_out",
            "clock_in",
        ]
    # Los eventos restaurados no bloquean los nuevos (la secuencia sigue siendo coherente).
    with session_scope() as db:
        db.add(TimeEvent(user_id=entries[1].user_id, entry_id=entries[1].id, kind="flag_pending", source="test"))
    with session_scope() as db:
        try:
            restore_logical(db, payload)
            raise AssertionError("debería rechazar una base no vacía")
        except Exception as exc:  # noqa: BLE001
            assert "no está vacía" in str(exc)


def test_originals_are_rebuilt_from_drive_with_hash_check(partners, fake):  # noqa: F811
    admin = partners["clients"][0]
    connect(admin)
    _populate(partners)
    drain()
    with session_scope() as db:
        v = db.execute(select(DocumentVersion)).scalar_one()
        path = blob_path(v.blob_path)
    path.unlink()  # disco local perdido
    with session_scope() as db:
        assert len(missing_blobs(db)) == 1
        assert rebuild_blobs_from_drive(db) == {"restored": 1, "failed": []}
    assert path.exists()


def test_concurrent_invitation_redemption_respects_max_uses(app, partners):
    from tests.conftest import UserClient, make_user

    c1 = partners["clients"][0]
    area = c1.post("/api/areas", json={"name": "Concurrencia"}).json()
    code = c1.post(f"/api/areas/{area['id']}/invitations", json={"max_uses": 1}).json()["code"]
    users = []
    for i in range(4):
        make_user(f"carrera{i}")
        users.append(UserClient(app, f"carrera{i}"))
    results: list[int] = []
    barrier = threading.Barrier(len(users))

    def go(u):
        barrier.wait()
        results.append(u.post("/api/invitations/redeem", json={"code": code}).status_code)

    threads = [threading.Thread(target=go, args=(u,)) for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 400, 400, 400]


def test_concurrent_note_saves_never_lose_versions(partners):
    c1, c2, c3 = partners["clients"]
    note = c1.post(
        "/api/notes",
        json={
            "area_id": partners["general"],
            "title": "Carrera",
            "content": "".join(f"línea {i}\n" for i in range(30)),
        },
    ).json()
    base = note["content"].splitlines(keepends=True)
    results: list[int] = []
    barrier = threading.Barrier(3)

    def edit(client, idx):
        lines = list(base)
        lines[idx] = f"editada por cliente {idx}\n"
        barrier.wait()
        r = client.put(
            f"/api/notes/{note['id']}", json={"base_version": 1, "title": "Carrera", "content": "".join(lines)}
        )
        results.append(r.status_code)

    threads = [threading.Thread(target=edit, args=(c, i)) for c, i in ((c1, 0), (c2, 10), (c3, 20))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200, 200, 200]  # cambios en zonas distintas: fusión sin pérdidas
    final = c1.get(f"/api/notes/{note['id']}").json()
    assert final["version"] == 4
    for idx in (0, 10, 20):
        assert f"editada por cliente {idx}" in final["content"]
