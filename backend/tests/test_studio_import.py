"""Importar proyectos propios en Project Studio: carpeta del equipo, archivos sueltos o ZIP."""

from __future__ import annotations

import io
import json
import stat
import zipfile
from pathlib import Path

from tests.conftest import UserClient, make_user
from tests.test_studio import create, start, wait_run
from wadima.studio import runner, workspace

PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"\x00\x01binario"


def _files(items: dict[str, bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (path, data, "application/octet-stream")) for path, data in items.items()]


def test_import_folder_into_empty_project(partners):
    c1, c2, _ = partners["clients"]
    sp = create(c1, partners["general"], "empty", "Mi aplicación")
    assert sp["template"] == "empty" and sp["profiles"] == ["console"]
    pkg = json.dumps({"name": "mi-app", "scripts": {"build": "vite build"}, "devDependencies": {"vite": "^8"}})
    folder = {
        "mi-app/package.json": pkg.encode(),
        "mi-app/index.html": b"<!doctype html><title>Mi app</title>",
        "mi-app/src/main.js": b"console.log('hola');\n",
        "mi-app/assets/logo.png": PNG,
        "mi-app/node_modules/vite/index.js": b"// dependencia: no se importa",
        "mi-app/.git/config": b"[core]\n\tfsmonitor = malicioso\n",
        "mi-app/.DS_Store": b"basura",
    }
    r = c1.post(f"/api/studio/projects/{sp['id']}/upload", files=_files(folder))
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["written"] == 4 and rep["skipped_ignored"] == 3 and rep["rejected_count"] == 0
    assert rep["stripped_root"] == "mi-app"  # la carpeta elegida no se anida dentro del proyecto
    # Se detecta un proyecto Vite: aparecen sus perfiles (dependencias, vista previa, build...).
    assert rep["template"] == "vite-vanilla" and "dev" in rep["project"]["profiles"]
    tree = {e["path"] for e in c1.get(f"/api/studio/projects/{sp['id']}/tree").json()}
    assert {"package.json", "index.html", "src/main.js", "assets/logo.png"} <= tree
    assert not any(p.startswith(("node_modules", ".git/")) for p in tree)
    src = c2.get(f"/api/studio/projects/{sp['id']}/file", params={"path": "src/main.js"}).json()
    assert src["content"] == "console.log('hola');\n"
    # Los cambios quedan pendientes de commit; el panel Git los confirma.
    changes = c1.get(f"/api/studio/projects/{sp['id']}/git/status").json()["changes"]
    assert {"index.html", "package.json", "src/", "assets/"} <= {c["path"] for c in changes}
    assert (
        c1.post(f"/api/studio/projects/{sp['id']}/git/commit", json={"message": "Importar mi proyecto"}).status_code
        == 200
    )


def test_upload_never_overwrites_unless_asked_and_rejects_unsafe_paths(partners, app):
    c1, c2, _ = partners["clients"]
    sp = create(c1, partners["general"], "static", "Sitio")
    r = c1.post(
        f"/api/studio/projects/{sp['id']}/upload",
        files=_files(
            {"index.html": b"<h1>Mio</h1>", "../fuera.txt": b"x", "C:/Windows/x.txt": b"x", "docs/a.md": b"# A"}
        ),
        data={"strip_root": "false"},
    )
    rep = r.json()
    assert rep["written"] == 1 and rep["skipped_existing"] == ["index.html"]
    assert {x["path"] for x in rep["rejected"]} == {"../fuera.txt", "C:/Windows/x.txt"}
    page = c1.get(f"/api/studio/projects/{sp['id']}/file", params={"path": "index.html"}).json()["content"]
    assert "Mio" not in page  # no se sustituyó
    r = c1.post(
        f"/api/studio/projects/{sp['id']}/upload",
        files=_files({"index.html": b"<h1>Mio</h1>"}),
        data={"overwrite": "true", "target": "", "strip_root": "false"},
    )
    assert r.json()["written"] == 1
    assert (
        c1.get(f"/api/studio/projects/{sp['id']}/file", params={"path": "index.html"}).json()["content"]
        == "<h1>Mio</h1>"
    )
    # A una subcarpeta concreta
    r = c1.post(
        f"/api/studio/projects/{sp['id']}/upload",
        files=_files({"nota.txt": b"hola"}),
        data={"target": "docs/extra"},
    )
    assert r.json()["written"] == 1
    # Un lector del área no puede subir; alguien ajeno no ve el proyecto.
    make_user("ajeno")
    outsider = UserClient(app, "ajeno")
    assert outsider.post(f"/api/studio/projects/{sp['id']}/upload", files=_files({"a.txt": b"x"})).status_code == 404
    assert c1.post(f"/api/studio/projects/{sp['id']}/upload").status_code == 422


def test_zip_import_with_limits(partners):
    c1, _, _ = partners["clients"]
    sp = create(c1, partners["general"], "empty", "Desde ZIP")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("proyecto/main.py", "print('importado')\n")
        zf.writestr("proyecto/test_main.py", "import unittest\n")
        zf.writestr("proyecto/../../escape.py", "x")
        link = zipfile.ZipInfo("proyecto/enlace")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
    r = c1.post(
        f"/api/studio/projects/{sp['id']}/upload",
        files={"archive": ("proyecto.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["written"] == 2 and rep["template"] == "python" and rep["rejected_count"] == 1
    tree = {e["path"] for e in c1.get(f"/api/studio/projects/{sp['id']}/tree").json()}
    assert {"main.py", "test_main.py"} <= tree and "enlace" not in tree
    run = wait_run(c1, sp["id"], start(c1, sp["id"], "run")["id"])
    assert run["status"] == "succeeded" and any("importado" in line for line in run["log"])
    bad = c1.post(
        f"/api/studio/projects/{sp['id']}/upload",
        files={"archive": ("x.zip", b"no es un zip", "application/zip")},
    )
    assert bad.status_code == 422 and "ZIP" in bad.json()["detail"]


def test_imported_vite_project_previews_under_its_path(tmp_path: Path):
    root = tmp_path / "mi-app"
    (root / "node_modules" / ".bin").mkdir(parents=True)
    argv = runner.build_argv("vite-vanilla", "dev", root, 5400, None)
    assert argv[-2:] == ["--base", "/p/mi-app/"]
    (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
    assert workspace.detect_kind(root) == "node"
    assert runner.build_argv("node", "test", root, None, None)[-2:] == ["run", "test"]
    try:
        runner.build_argv("node", "build", root, None, None)
        raise AssertionError("debería fallar sin script build")
    except runner.RunError as exc:
        assert "build" in str(exc)
