from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import UserClient, make_user
from wadima.config import REPO_ROOT, get_settings
from wadima.studio.preview import preview_app


def create(c, area, template="python", name="Calculadora"):
    r = c.post("/api/studio/projects", json={"area_id": area, "name": name, "template": template})
    assert r.status_code == 200, r.text
    return r.json()


def wait_run(c, sp_id, run_id, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/api/studio/projects/{sp_id}/runs/{run_id}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.3)
    raise AssertionError("La ejecución no terminó a tiempo")


def start(c, sp_id, profile, command=None):
    r = c.post(f"/api/studio/projects/{sp_id}/runs", json={"profile": profile, "command": command})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_edit_conflicts_and_git(partners):
    c1, c2, _ = partners["clients"]
    sp = create(c1, partners["general"])
    tree = {e["path"] for e in c1.get(f"/api/studio/projects/{sp['id']}/tree").json()}
    assert {"main.py", "calculos.py", "test_calculos.py"} <= tree and ".git" not in tree
    f = c1.get(f"/api/studio/projects/{sp['id']}/file?path=calculos.py").json()
    # Guardado normal
    edited = f["content"] + "\n\ndef suma(a, b):\n    return a + b\n"
    r = c1.put(
        f"/api/studio/projects/{sp['id']}/file", json={"path": "calculos.py", "content": edited, "base_hash": f["hash"]}
    )
    assert r.status_code == 200
    # Otro socio con la versión antigua: sin contenido base -> 409 (nada se sobrescribe en silencio)
    stale = c2.put(
        f"/api/studio/projects/{sp['id']}/file",
        json={"path": "calculos.py", "content": "x = 1\n", "base_hash": f["hash"]},
    )
    assert stale.status_code == 409 and "suma" in stale.json()["detail"]["current"]
    # Con contenido base y cambios en zonas distintas -> fusión a tres bandas
    theirs = "# Módulo de cálculos\n" + f["content"]
    merged = c2.put(
        f"/api/studio/projects/{sp['id']}/file",
        json={"path": "calculos.py", "content": theirs, "base_hash": f["hash"], "base_content": f["content"]},
    )
    assert merged.status_code == 200 and merged.json()["merged"] is True
    assert "# Módulo de cálculos" in merged.json()["content"] and "def suma" in merged.json()["content"]
    # Git: commit con autoría, historial, diff, versión anterior y restauración
    status = c1.get(f"/api/studio/projects/{sp['id']}/git/status").json()
    assert status["branch"] == "main" and status["changes"]
    assert c2.post(f"/api/studio/projects/{sp['id']}/git/commit", json={"message": "Añade suma"}).status_code == 200
    log = c1.get(f"/api/studio/projects/{sp['id']}/git/log").json()
    assert len(log) == 2 and log[0]["author"] == "Socio2" and log[0]["message"] == "Añade suma"
    old = c1.get(f"/api/studio/projects/{sp['id']}/git/show?ref={log[1]['hash']}&path=calculos.py").json()
    assert "def suma" not in old["content"]
    diff = c1.get(f"/api/studio/projects/{sp['id']}/git/diff?ref_from={log[1]['hash']}&ref_to={log[0]['hash']}").json()
    assert "+def suma" in diff["diff"]
    c1.post(f"/api/studio/projects/{sp['id']}/git/restore", json={"ref": log[1]["hash"], "path": "calculos.py"})
    assert "def suma" not in c1.get(f"/api/studio/projects/{sp['id']}/file?path=calculos.py").json()["content"]
    # Con cambios sin confirmar no se puede cambiar de rama
    assert c1.post(f"/api/studio/projects/{sp['id']}/git/branches", json={"name": "experimento"}).status_code == 200
    assert c1.post(f"/api/studio/projects/{sp['id']}/git/checkout", json={"name": "experimento"}).status_code == 409
    assert c1.post(f"/api/studio/projects/{sp['id']}/git/commit", json={"message": "Vuelve atrás"}).status_code == 200
    assert c1.post(f"/api/studio/projects/{sp['id']}/git/checkout", json={"name": "experimento"}).status_code == 200


def test_path_safety(partners):
    c1 = partners["clients"][0]
    sp = create(c1, partners["general"])
    base = f"/api/studio/projects/{sp['id']}"
    for bad in ("../../secreto.txt", "/etc/passwd", "C:/Windows/win.ini", ".git/config", "a/../../x"):
        assert c1.get(f"{base}/file", params={"path": bad}).status_code == 400, bad
        assert c1.put(f"{base}/file", json={"path": bad, "content": "x", "base_hash": None}).status_code == 400, bad
    assert c1.post(f"{base}/files", json={"path": "src/util.py"}).status_code == 200
    assert c1.post(f"{base}/files/rename", json={"src": "src/util.py", "dst": "../fuera.py"}).status_code == 400


def test_runs_tests_and_sandbox_limits(partners, monkeypatch):
    c1 = partners["clients"][0]
    sp = create(c1, partners["general"])
    r = wait_run(c1, sp["id"], start(c1, sp["id"], "test")["id"])
    assert r["status"] == "succeeded" and r["exit_code"] == 0
    assert any("OK" in line for line in r["log"]) and any("Job Object" in line for line in r["log"])
    run = wait_run(c1, sp["id"], start(c1, sp["id"], "run")["id"])
    assert any("Media: 6.0" in line for line in run["log"])
    # Entorno sin secretos del servidor
    env = wait_run(
        c1,
        sp["id"],
        start(
            c1,
            sp["id"],
            "console",
            "python -c \"import os; print('SECRETOS', sorted(k for k in os.environ "
            "if k.startswith(('WADIMA', 'ANTHROPIC', 'GOOGLE'))))\"",
        )["id"],
    )
    assert any("SECRETOS []" in line for line in env["log"])
    # Límite de memoria (256 MB) y de tiempo (3 s)
    s = get_settings()
    monkeypatch.setattr(s, "run_memory_mb", 256)
    mem = wait_run(c1, sp["id"], start(c1, sp["id"], "console", 'python -c "x = bytearray(600 * 1024 * 1024)"')["id"])
    assert mem["status"] == "failed" and mem["exit_code"] != 0
    monkeypatch.setattr(s, "run_timeout_seconds", 3)
    slow = wait_run(c1, sp["id"], start(c1, sp["id"], "console", 'python -c "import time; time.sleep(60)"')["id"])
    assert slow["status"] == "timeout"
    acts = c1.get(f"/api/activity?area_id={partners['general']}").json()
    assert any(a["kind"] == "studio.test" for a in acts)


PROBE = """import sys
for p in sys.argv[1:3]:
    try:
        open(p).read()
        print("FUGA-LECTURA", p)
    except OSError:
        print("LECTURA-DENEGADA")
try:
    open(sys.argv[3], "w").write("x")
    print("FUGA-ESCRITURA")
except OSError:
    print("ESCRITURA-DENEGADA")
open("dentro.txt", "w").write("ok")
print("ESCRITURA-DENTRO")
"""

HOOK = """import pathlib, sys
try:
    pathlib.Path(sys.argv[1]).write_text("escapado")
except OSError:
    pass
"""


@pytest.mark.skipif(os.name != "nt", reason="Aislamiento por niveles de integridad de Windows")
def test_sandbox_isolates_server_data_and_git(partners):
    c1 = partners["clients"][0]
    sp = create(c1, partners["general"])
    base = f"/api/studio/projects/{sp['id']}"
    s = get_settings()
    secret = s.data_dir / "secrets" / "prueba-sandbox.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("no debe leerse desde Studio")
    outside = s.data_dir / "escrito-desde-studio.txt"
    for path, content in (("probe.py", PROBE), ("hook.py", HOOK)):
        assert c1.put(f"{base}/file", json={"path": path, "content": content, "base_hash": None}).status_code == 200
    cmd = f'python probe.py "{secret.as_posix()}" "{(REPO_ROOT / ".env").as_posix()}" "{outside.as_posix()}"'
    log = wait_run(c1, sp["id"], start(c1, sp["id"], "console", cmd)["id"])["log"]
    assert sum("LECTURA-DENEGADA" in line for line in log) == 2, log
    assert any("ESCRITURA-DENEGADA" in line for line in log) and any("ESCRITURA-DENTRO" in line for line in log)
    assert not any("FUGA" in line for line in log) and not outside.exists()
    # Una configuración de Git maliciosa escrita por el código del proyecto solo se ejecuta dentro del sandbox
    root = s.studio_dir / sp["slug"]
    hook = f'"{Path(sys.executable).as_posix()}" "{(root / "hook.py").as_posix()}" "{outside.as_posix()}"'
    cfg = f"open('.git/config', 'a').write('[core]\\n\\tfsmonitor = ' + {hook!r} + '\\n')\n"
    assert c1.put(f"{base}/file", json={"path": "cfg.py", "content": cfg, "base_hash": None}).status_code == 200
    assert wait_run(c1, sp["id"], start(c1, sp["id"], "console", "python cfg.py")["id"])["status"] == "succeeded"
    assert "fsmonitor" in (root / ".git" / "config").read_text()
    # Con Git fuera del sandbox este hook se ejecutaba y escribía en el directorio de datos.
    assert c1.get(f"{base}/git/status").status_code == 200
    assert not outside.exists()


def test_console_restrictions(partners):
    c1 = partners["clients"][0]
    sp = create(c1, partners["general"])
    base = f"/api/studio/projects/{sp['id']}/runs"
    for cmd in (
        "rm -rf /",
        "cmd /c dir",
        "powershell Get-Process",
        "python -V && whoami",
        "git commit -m x",
        "python -c 1 | more",
        "",
    ):
        r = c1.post(base, json={"profile": "console", "command": cmd})
        assert r.status_code == 400, cmd
    assert c1.post(base, json={"profile": "dev"}).status_code == 400  # perfil no disponible para Python


def test_permissions(app, partners):
    c1 = partners["clients"][0]
    make_user("lector")
    make_user("ajeno")
    lector, ajeno = UserClient(app, "lector"), UserClient(app, "ajeno")
    area = c1.post("/api/areas", json={"name": "Código"}).json()
    c1.put(f"/api/areas/{area['id']}/members/{lector.id}", json={"role": "viewer"})
    sp = create(c1, area["id"])
    base = f"/api/studio/projects/{sp['id']}"
    assert lector.get(f"{base}/file?path=main.py").status_code == 200
    assert lector.put(f"{base}/file", json={"path": "main.py", "content": "x", "base_hash": None}).status_code == 403
    assert lector.post(f"{base}/runs", json={"profile": "test"}).status_code == 403
    assert ajeno.get(f"{base}/tree").status_code == 404
    assert (
        lector.post(
            "/api/studio/projects", json={"area_id": area["id"], "name": "Otro", "template": "static"}
        ).status_code
        == 403
    )


def test_static_preview_requires_membership_and_reloads(app, partners):
    c1 = partners["clients"][0]
    make_user("ajeno")
    ajeno = UserClient(app, "ajeno")
    sp = create(c1, partners["general"], template="static", name="Sitio")
    assert sp["preview_url"].endswith(f"/p/{sp['slug']}/") and sp["preview"]["available"]
    with TestClient(preview_app) as pv:  # el lifespan enlaza el bucle para las notificaciones de recarga
        assert pv.get(f"/p/{sp['slug']}/").status_code == 401
        pv.cookies.set("wadima_session", ajeno.http.cookies.get("wadima_session"))
        assert pv.get(f"/p/{sp['slug']}/").status_code == 404
        pv.cookies.set("wadima_session", c1.http.cookies.get("wadima_session"))
        page = pv.get(f"/p/{sp['slug']}/")
        assert page.status_code == 200 and "Sitio estático de WADIMA" in page.text
        assert "/__wadima/reload/" in page.text and "frame-ancestors" in page.headers["content-security-policy"]
        assert pv.get(f"/p/{sp['slug']}/style.css").headers["content-type"].startswith("text/css")
        assert pv.get(f"/p/{sp['slug']}/..%2F..%2Fsecretos.txt").status_code in (400, 404)
        with pv.websocket_connect(f"/__wadima/reload/{sp['slug']}") as ws:
            f = c1.get(f"/api/studio/projects/{sp['id']}/file?path=index.html").json()
            c1.put(
                f"/api/studio/projects/{sp['id']}/file",
                json={
                    "path": "index.html",
                    "content": f["content"].replace("estático", "en vivo"),
                    "base_hash": f["hash"],
                },
            )
            assert ws.receive_text() == "reload"
        assert "Sitio en vivo de WADIMA" in pv.get(f"/p/{sp['slug']}/").text


@pytest.mark.network
@pytest.mark.skipif(not shutil.which("pnpm") and not shutil.which("npm"), reason="Node no disponible")
def test_vite_dev_server_live_preview_through_proxy(partners):
    """Prueba real: instala Vite, arranca el servidor de desarrollo y sirve la vista previa por el proxy."""
    c1 = partners["clients"][0]
    sp = create(c1, partners["general"], template="vite-vanilla", name="Web Vite")
    inst = wait_run(c1, sp["id"], start(c1, sp["id"], "install")["id"], timeout=240)
    assert inst["status"] == "succeeded", "\n".join(inst["log"][-30:])
    tests = wait_run(c1, sp["id"], start(c1, sp["id"], "test")["id"])
    assert tests["status"] == "succeeded"
    dev = start(c1, sp["id"], "dev")
    deadline = time.time() + 60
    while time.time() < deadline:
        state = c1.get(f"/api/studio/projects/{sp['id']}").json()["preview"]
        if state["dev_ready"]:
            break
        time.sleep(0.5)
    assert state["dev_ready"], c1.get(f"/api/studio/projects/{sp['id']}/runs/{dev['id']}").json()["log"]
    with TestClient(preview_app) as pv:
        pv.cookies.set("wadima_session", c1.http.cookies.get("wadima_session"))
        page = pv.get(f"/p/{sp['slug']}/")
        assert page.status_code == 200 and "Hola desde WADIMA" in page.text and "/@vite/client" in page.text
        js = pv.get(f"/p/{sp['slug']}/main.js")
        assert js.status_code == 200 and "incrementar" in js.text
    build = wait_run(c1, sp["id"], start(c1, sp["id"], "build")["id"], timeout=120)
    assert build["status"] == "succeeded"
    assert c1.post(f"/api/studio/projects/{sp['id']}/runs/{dev['id']}/stop").json()["stopped"] is True
    final = wait_run(c1, sp["id"], dev["id"])
    assert final["status"] == "stopped"
