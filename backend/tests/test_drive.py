from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select, text

from tests.fakedrive import FakeDrive
from wadima.config import get_settings
from wadima.db import session_scope
from wadima.drive import client as drive_client
from wadima.drive.client import RESUMABLE_THRESHOLD
from wadima.models import DriveConnection, DriveFile
from wadima.services import jobs


@pytest.fixture
def fake(monkeypatch):
    fd = FakeDrive()
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_client_secret", "test-secret")
    monkeypatch.setattr(s, "public_origin", "http://127.0.0.1:8000")
    monkeypatch.setattr(drive_client, "TRANSPORT_OVERRIDE", fd.transport())
    monkeypatch.setattr(drive_client, "SLEEP", lambda _s: None)
    import wadima.drive.providers  # noqa: F401 - registra proveedores

    return fd


def connect(admin) -> str:
    r = admin.post("/api/drive/connect")
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["auth_url"])
    params = parse_qs(url.query)
    assert params["code_challenge_method"] == ["S256"] and params["access_type"] == ["offline"]
    assert params["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    state = params["state"][0]
    cb = admin.get(f"/api/drive/oauth/callback?state={state}&code=good-code", follow_redirects=False)
    assert cb.status_code == 303 and cb.headers["location"].endswith("drive=connected"), cb.headers.get("location")
    return state


def drain():
    """Procesa todos los trabajos pendientes como si hubiera pasado el tiempo de espera."""
    make_due()
    return jobs.drain(max_jobs=50)


def make_due():
    with session_scope() as db:
        db.execute(text("UPDATE jobs SET run_after = now() WHERE status = 'queued'"))
        db.execute(text("UPDATE drive_files SET next_attempt_at = now()"))


def note(c, area, content="Hola Drive\n", title="Nota Drive"):
    return c.post("/api/notes", json={"area_id": area, "title": title, "content": content}).json()


def row_for(local_id) -> DriveFile:
    with session_scope() as db:
        return db.execute(select(DriveFile).where(DriveFile.local_id == str(local_id))).scalar_one()


def test_oauth_flow_stores_encrypted_tokens_and_creates_structure(partners, fake):
    admin = partners["clients"][0]
    state = connect(admin)
    with session_scope() as db:
        conn = db.execute(select(DriveConnection)).scalar_one()
        assert conn.status == "connected" and conn.google_email == "central@example.com"
        assert b"refresh" not in conn.token_encrypted and b"ya29" not in conn.token_encrypted
    root = fake.folder_named("WADIMA")
    for name in ("Workspace", "Areas", "Projects", "Library", "Processed", "Markdown", "Memory", "Reports", "Backups"):
        folder = fake.folder_named(name)
        assert folder is not None and folder.parents == [root.id]
    # El estado OAuth es de un solo uso
    again = admin.get(f"/api/drive/oauth/callback?state={state}&code=good-code", follow_redirects=False)
    assert again.headers["location"].endswith("drive=invalid_state")
    # Un miembro no puede conectar la unidad central
    assert partners["clients"][1].post("/api/drive/connect").status_code == 403
    status = admin.get("/api/drive/status").json()
    assert status["connection"]["status"] == "connected"


def test_invalid_code_is_reported(partners, fake):
    admin = partners["clients"][0]
    r = admin.post("/api/drive/connect").json()
    state = parse_qs(urlparse(r["auth_url"]).query)["state"][0]
    cb = admin.get(f"/api/drive/oauth/callback?state={state}&code=bad", follow_redirects=False)
    assert cb.headers["location"].endswith("drive=error")
    assert admin.get("/api/drive/summary").json()["connected"] is False


def test_pending_while_disconnected_then_synced_and_verified(partners, fake):
    admin, c2, _ = partners["clients"]
    n = note(c2, partners["general"])
    drain()
    assert row_for(n["id"]).sync_state == "pending"  # nunca se marca sincronizado sin subir
    assert admin.get("/api/drive/summary").json()["status"] == "disconnected"
    connect(admin)
    make_due()
    drain()
    row = row_for(n["id"])
    assert row.sync_state == "synced" and row.drive_file_id
    remote = fake.files[row.drive_file_id]
    assert remote.content == b"Hola Drive\n"
    assert remote.meta()["md5Checksum"] == row.local_md5 == row.base_md5
    assert fake.files[remote.parents[0]].name == "general"  # Workspace/general
    assert admin.get("/api/drive/summary").json()["status"] == "synced"
    # Edición local -> mismo ID estable de Drive, nueva versión
    c2.put(f"/api/notes/{n['id']}", json={"base_version": 1, "title": n["title"], "content": "Cambio local\n"})
    make_due()
    drain()
    row2 = row_for(n["id"])
    assert row2.drive_file_id == row.drive_file_id and fake.files[row.drive_file_id].content == b"Cambio local\n"
    assert len(fake.by_ref(f"note:{n['id']}")) == 1


def test_network_failures_retry_without_data_loss(partners, fake):
    admin, c2, _ = partners["clients"]
    connect(admin)
    n = note(c2, partners["general"], "Contenido valioso\n")
    fake.fail_next(2, 503)  # transitorio: el cliente reintenta
    drain()
    assert row_for(n["id"]).sync_state == "synced"
    c2.put(f"/api/notes/{n['id']}", json={"base_version": 1, "title": n["title"], "content": "Versión 2\n"})
    fake.fail_next(40, "network")  # caída prolongada
    make_due()
    drain()
    row = row_for(n["id"])
    assert row.sync_state == "error" and "red" in row.last_error
    assert fake.files[row.drive_file_id].content == b"Contenido valioso\n"  # Drive conserva la anterior
    fake.failures.clear()
    make_due()
    drain()
    row = row_for(n["id"])
    assert row.sync_state == "synced" and fake.files[row.drive_file_id].content == b"Versi\xc3\xb3n 2\n"


def test_remote_change_is_imported_and_conflicts_are_flagged(partners, fake):
    admin, c2, _ = partners["clients"]
    connect(admin)
    n = note(c2, partners["general"], "Base\n")
    drain()
    fid = row_for(n["id"]).drive_file_id
    # Cambio hecho directamente en Drive -> reconciliación -> se importa como nueva versión de la nota
    fake.remote_edit(fid, b"Editado en Drive\n")
    admin.post("/api/drive/reconcile")
    drain()
    assert c2.get(f"/api/notes/{n['id']}").json()["content"] == "Editado en Drive\n"
    revs = c2.get(f"/api/notes/{n['id']}/revisions").json()
    assert revs[0]["version"] == 2
    # Cambio local y remoto a la vez -> conflicto, sin sobrescribir
    fake.remote_edit(fid, b"Remoto otra vez\n")
    c2.put(f"/api/notes/{n['id']}", json={"base_version": 2, "title": n["title"], "content": "Local otra vez\n"})
    make_due()
    drain()
    row = row_for(n["id"])
    assert row.sync_state == "conflict" and row.conflict["remote_md5"]
    assert fake.files[fid].content == b"Remoto otra vez\n"
    assert any(x["kind"] == "sync.conflict" for x in admin.get("/api/notifications").json()["items"])
    assert admin.get("/api/drive/summary").json()["status"] == "conflict"
    # Resolver conservando la versión local
    assert admin.post(f"/api/drive/files/{row.id}/resolve", json={"strategy": "keep_local"}).status_code == 200
    drain()
    assert row_for(n["id"]).sync_state == "synced" and fake.files[fid].content == b"Local otra vez\n"
    # Nuevo conflicto resuelto conservando la remota
    fake.remote_edit(fid, b"Gana Drive\n")
    cur = c2.get(f"/api/notes/{n['id']}").json()
    c2.put(f"/api/notes/{n['id']}", json={"base_version": cur["version"], "title": n["title"], "content": "Pierde\n"})
    make_due()
    drain()
    row = row_for(n["id"])
    assert row.sync_state == "conflict"
    admin.post(f"/api/drive/files/{row.id}/resolve", json={"strategy": "keep_remote"})
    drain()
    assert c2.get(f"/api/notes/{n['id']}").json()["content"] == "Gana Drive\n"
    assert row_for(n["id"]).sync_state == "synced"


def test_remote_trash_and_recreate(partners, fake):
    admin, c2, _ = partners["clients"]
    connect(admin)
    n = note(c2, partners["general"])
    drain()
    old = row_for(n["id"]).drive_file_id
    fake.remote_trash(old)
    admin.post("/api/drive/reconcile")
    row = row_for(n["id"])
    assert row.sync_state == "remote_deleted"
    assert admin.post(f"/api/drive/files/{row.id}/retry").json()["sync_state"] == "pending"
    drain()
    new = row_for(n["id"])
    assert new.sync_state == "synced" and new.drive_file_id != old


def test_recovery_adopts_file_uploaded_before_crash(partners, fake):
    admin, c2, _ = partners["clients"]
    connect(admin)
    n = note(c2, partners["general"], "Texto\n")
    # Simula una subida anterior que llegó a Drive pero cuyo resultado no se registró (caída del proceso)
    ws = fake.folder_named("Workspace")
    fake._new("huérfano.md", "text/markdown", [ws.id], b"Texto\n", {"wadimaRef": f"note:{n['id']}"})
    drain()
    assert len(fake.by_ref(f"note:{n['id']}")) == 1
    assert row_for(n["id"]).sync_state == "synced"


def test_resumable_upload_survives_interruption(partners, fake, monkeypatch):
    import httpx

    from wadima.drive import sync as drive_sync

    admin = partners["clients"][0]
    connect(admin)
    big = os.urandom(RESUMABLE_THRESHOLD + 700_000)  # > 5 MB -> subida reanudable por fragmentos
    monkeypatch.setitem(drive_sync.PROVIDERS, "test_blob", lambda db, row: (big, "application/octet-stream"))
    with session_scope() as db:
        drive_sync.track(
            db,
            "test_blob",
            "grande",
            area_id=None,
            folder_key="Library",
            name="grande.bin",
            mime="application/octet-stream",
            content=big,
        )
    original = fake.handle
    calls = {"n": 0}

    def flaky(request):
        if request.method == "PUT" and "upload_id" in str(request.url):
            calls["n"] += 1
            if calls["n"] == 2:
                raise httpx.ConnectError("corte a mitad de subida", request=request)
        return original(request)

    monkeypatch.setattr(drive_client, "TRANSPORT_OVERRIDE", httpx.MockTransport(flaky))
    drain()
    row = row_for("grande")
    assert row.sync_state == "synced", row.last_error
    assert fake.files[row.drive_file_id].content == big
    assert calls["n"] >= 3 and fake.sessions  # hubo sesión reanudable y se reanudó tras el corte


def test_token_refresh_and_revocation(partners, fake):
    admin, c2, _ = partners["clients"]
    connect(admin)
    fake.expire_access_tokens()  # el token de acceso deja de ser válido
    n = note(c2, partners["general"])
    drain()
    assert row_for(n["id"]).sync_state == "synced"  # se renovó con el refresh token
    r = admin.post("/api/drive/disconnect").json()
    assert r["revoked_at_google"] is True
    with session_scope() as db:
        conn = db.execute(select(DriveConnection)).scalar_one()
        assert conn.status == "disconnected" and conn.token_encrypted is None


def test_connection_test_permissions_and_share(partners, fake):
    admin, c2, _ = partners["clients"]
    connect(admin)
    result = admin.post("/api/drive/test").json()
    assert result["ok"] is True and [s["step"] for s in result["steps"]][-1] == "limpieza"
    perms = admin.get("/api/drive/permissions").json()
    assert all(p["access"] is None for p in perms["partners"])
    assert admin.post(f"/api/drive/share/{c2.id}").status_code == 422  # sin correo configurado
    c2.patch("/api/users/me", json={"email": "socio2@example.com"})
    assert admin.post(f"/api/drive/share/{c2.id}").status_code == 200
    perms = admin.get("/api/drive/permissions").json()
    assert next(p for p in perms["partners"] if p["user_id"] == c2.id)["access"] == "writer"


def test_backup_excludes_secrets(partners, fake):
    admin = partners["clients"][0]
    connect(admin)
    admin.post("/api/drive/backup")
    drain()
    backup = next(f for f in fake.files.values() if f.name.startswith("wadima-backup-"))
    text_content = backup.content.decode()
    assert "argon2" not in text_content and "password_hash" not in text_content
    assert "token" not in text_content.lower() or "token_encrypted" not in text_content
    anchor = next(f for f in fake.files.values() if f.name == "audit-anchor.json")
    assert '"chain_ok": true' in anchor.content.decode()
    assert os.environ["WADIMA_ENV"] == "test"
