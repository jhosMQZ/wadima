"""Espacios de trabajo de Project Studio: rutas seguras, plantillas y operaciones de archivos."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from wadima.config import get_settings

IGNORED_DIRS = {".git", "node_modules", "dist", "__pycache__", ".venv", ".tmp", ".home", ".pytest_cache", ".vite"}
MAX_FILE_BYTES = 1_000_000
MAX_TREE_ENTRIES = 3000


class WorkspaceError(Exception):
    pass


def project_root(slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,79}", slug):
        raise WorkspaceError("Identificador de proyecto no válido")
    return (get_settings().studio_dir / slug).resolve()


def safe_path(root: Path, rel: str, *, allow_root: bool = False) -> Path:
    """Resuelve una ruta relativa dentro del proyecto. Rechaza rutas absolutas, '..', .git y enlaces."""
    rel = (rel or "").replace("\\", "/").strip()
    if rel.startswith("/") or re.match(r"^[a-zA-Z]:", rel) or "\x00" in rel:
        raise WorkspaceError("Ruta no permitida")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise WorkspaceError("Ruta no permitida")
    if parts and parts[0] in (".git",):
        raise WorkspaceError("El directorio .git no se puede editar desde el editor")
    if not parts and not allow_root:
        raise WorkspaceError("Ruta vacía")
    target = root.joinpath(*parts)
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise WorkspaceError("Ruta fuera del proyecto")
    # Ningún componente puede ser un enlace simbólico o una unión (evita escapar del proyecto).
    probe = root
    for p in parts:
        probe = probe / p
        if probe.is_symlink() or (os.name == "nt" and probe.exists() and _is_junction(probe)):
            raise WorkspaceError("No se permiten enlaces simbólicos")
    return target


def _is_junction(path: Path) -> bool:
    try:
        return path.is_junction()  # Python 3.12+
    except AttributeError:
        return False


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree(root: Path) -> list[dict]:
    out: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in IGNORED_DIRS and not Path(dirpath, d).is_symlink() and not _is_junction(Path(dirpath, d))
        )
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if rel_dir != ".":
            out.append({"path": rel_dir, "type": "dir"})
        for f in sorted(filenames):
            p = Path(dirpath, f)
            if p.is_symlink():
                continue
            rel = p.relative_to(root).as_posix()
            out.append({"path": rel, "type": "file", "size": p.stat().st_size})
            if len(out) >= MAX_TREE_ENTRIES:
                return out
    return out


def read_text(root: Path, rel: str) -> tuple[str, str]:
    p = safe_path(root, rel)
    if not p.is_file():
        raise WorkspaceError("El archivo no existe")
    data = p.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise WorkspaceError("Archivo demasiado grande para el editor (máx. 1 MB)")
    if b"\x00" in data[:8192]:
        raise WorkspaceError("Archivo binario: no se puede editar como texto")
    try:
        return data.decode("utf-8"), content_hash(data)
    except UnicodeDecodeError as exc:
        raise WorkspaceError("El archivo no está en UTF-8") from exc


def write_text(root: Path, rel: str, content: str) -> str:
    p = safe_path(root, rel)
    data = content.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise WorkspaceError("Contenido demasiado grande (máx. 1 MB)")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.wadima-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)
    return content_hash(data)


def current_hash(root: Path, rel: str) -> str | None:
    p = safe_path(root, rel)
    return content_hash(p.read_bytes()) if p.is_file() else None


def delete_path(root: Path, rel: str) -> None:
    p = safe_path(root, rel)
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    else:
        raise WorkspaceError("No existe")


def rename_path(root: Path, src: str, dst: str) -> None:
    a, b = safe_path(root, src), safe_path(root, dst)
    if not a.exists():
        raise WorkspaceError("El origen no existe")
    if b.exists():
        raise WorkspaceError("El destino ya existe")
    b.parent.mkdir(parents=True, exist_ok=True)
    a.rename(b)


# --------------------------------------------------------------------------- plantillas
VITE_CONFIG = """import { defineConfig } from "vite";

// WADIMA publica la vista previa en /p/<proyecto>/ a través de su proxy autenticado.
export default defineConfig({
  base: process.env.WADIMA_BASE || "/",
  server: { host: "127.0.0.1", strictPort: true, allowedHosts: true },
});
"""

TEMPLATES: dict[str, dict] = {
    "vite-vanilla": {
        "name": "Web (Vite + JavaScript)",
        "needs": ["node"],
        "files": {
            "package.json": json.dumps(
                {
                    "name": "proyecto-wadima",
                    "private": True,
                    "version": "0.1.0",
                    "type": "module",
                    "scripts": {"dev": "vite", "build": "vite build", "test": "node --test"},
                    "devDependencies": {"vite": "^8.3.5"},
                },
                indent=2,
            )
            + "\n",
            "vite.config.js": VITE_CONFIG,
            "index.html": """<!doctype html>
<html lang="es">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Proyecto __BRAND__</title>
    <link rel="stylesheet" href="./style.css" />
  </head>
  <body>
    <main id="app">
      <h1 id="titulo">Hola desde __BRAND__</h1>
      <p>Edite <code>main.js</code> o <code>index.html</code> y la vista previa se actualizará.</p>
      <button id="contador">Clics: 0</button>
    </main>
    <script type="module" src="./main.js"></script>
  </body>
</html>
""",
            "main.js": """import { incrementar } from "./contador.js";

const boton = document.querySelector("#contador");
let clics = 0;
boton.addEventListener("click", () => {
  clics = incrementar(clics);
  boton.textContent = `Clics: ${clics}`;
});
""",
            "contador.js": "export function incrementar(n) {\n  return n + 1;\n}\n",
            "contador.test.js": """import { test } from "node:test";
import assert from "node:assert/strict";
import { incrementar } from "./contador.js";

test("incrementar suma uno", () => {
  assert.equal(incrementar(1), 2);
});
""",
            "style.css": "body { font-family: system-ui, sans-serif; margin: 2rem; }\nh1 { color: #2f7fb8; }\n",
            ".gitignore": "node_modules/\ndist/\n.tmp/\n.home/\n",
        },
    },
    "static": {
        "name": "Web estática (HTML/CSS/JS, sin Node)",
        "needs": [],
        "files": {
            "index.html": """<!doctype html>
<html lang="es">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Sitio estático</title>
    <link rel="stylesheet" href="style.css" />
  </head>
  <body>
    <h1 id="titulo">Sitio estático de __BRAND__</h1>
    <script src="app.js"></script>
  </body>
</html>
""",
            "style.css": "body { font-family: system-ui, sans-serif; margin: 2rem; }\n",
            "app.js": "console.log('Hola desde __BRAND__');\n",
            ".gitignore": ".tmp/\n.home/\n",
        },
    },
    "empty": {
        "name": "Proyecto vacío (para importar o empezar desde cero)",
        "needs": [],
        "files": {".gitignore": "node_modules/\ndist/\n__pycache__/\n.venv/\n.tmp/\n.home/\n"},
    },
    "node": {
        "name": "Node.js (proyecto importado)",
        "needs": ["node"],
        "files": {},
        "create": False,  # solo se asigna al detectar un proyecto importado
    },
    "python": {
        "name": "Python (script + pruebas unittest)",
        "needs": ["python"],
        "files": {
            "main.py": 'from calculos import media\n\nif __name__ == "__main__":\n    print("Media:", media([4, 8, 6]))\n',  # noqa: E501
            "calculos.py": 'def media(valores):\n    if not valores:\n        raise ValueError("lista vacía")\n'
            "    return sum(valores) / len(valores)\n",
            "test_calculos.py": "import unittest\n\nfrom calculos import media\n\n\nclass PruebasMedia(unittest.TestCase):\n"  # noqa: E501
            "    def test_media(self):\n        self.assertEqual(media([2, 4]), 3)\n\n"
            "    def test_vacia(self):\n        with self.assertRaises(ValueError):\n"
            '            media([])\n\n\nif __name__ == "__main__":\n    unittest.main()\n',
            ".gitignore": "__pycache__/\n.tmp/\n.home/\n",
        },
    },
}


def create_from_template(slug: str, template: str, brand: str = "WADIMA") -> Path:
    """Crea el proyecto con el nombre comercial de la organización en los textos visibles de la plantilla."""
    if template not in TEMPLATES:
        raise WorkspaceError("Plantilla desconocida")
    root = project_root(slug)
    if root.exists():
        raise WorkspaceError("Ya existe un espacio de trabajo con ese identificador")
    root.mkdir(parents=True)
    for rel, content in TEMPLATES[template]["files"].items():
        write_text(root, rel, content.replace("__BRAND__", brand))
    return root


# --------------------------------------------------------------------------- importación de proyectos propios
IMPORT_MAX_FILE_BYTES = 25 * 1024 * 1024
IMPORT_MAX_TOTAL_BYTES = 300 * 1024 * 1024
IMPORT_MAX_ENTRIES = 10_000
IGNORED_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}


@dataclass
class ImportReport:
    written: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    skipped_ignored: int = 0
    rejected: list[dict[str, str]] = field(default_factory=list)
    bytes: int = 0

    def out(self) -> dict:
        return {
            "written": len(self.written),
            "skipped_existing": self.skipped_existing[:200],
            "skipped_existing_count": len(self.skipped_existing),
            "skipped_ignored": self.skipped_ignored,
            "rejected": self.rejected[:200],
            "rejected_count": len(self.rejected),
            "bytes": self.bytes,
        }


def _parts(rel: str) -> list[str]:
    return [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]


def common_root(paths: list[str]) -> str:
    """Carpeta común de primer nivel (al subir una carpeta el navegador antepone su nombre a cada ruta)."""
    firsts = {tuple(_parts(p)[:1]) for p in paths if len(_parts(p)) > 1}
    singles = [p for p in paths if len(_parts(p)) <= 1]
    if len(firsts) == 1 and not singles:
        return next(iter(firsts))[0]
    return ""


def import_items(
    root: Path,
    items: Iterable[tuple[str, bytes]],
    *,
    target: str = "",
    overwrite: bool = False,
    strip: str = "",
) -> ImportReport:
    """Escribe archivos dentro del proyecto con rutas seguras. Omite dependencias y metadatos (node_modules, .git…)
    y, salvo `overwrite`, nunca sustituye un archivo existente."""
    report = ImportReport()
    base = _parts(target)
    for rel, data in items:
        parts = _parts(rel)
        if strip and parts[:1] == [strip]:
            parts = parts[1:]
        if not parts:
            continue
        shown = "/".join(base + parts)
        if any(p in IGNORED_DIRS for p in parts[:-1]) or parts[-1] in IGNORED_FILES or parts[0] == ".git":
            report.skipped_ignored += 1
            continue
        if len(data) > IMPORT_MAX_FILE_BYTES:
            report.rejected.append({"path": shown, "reason": "supera 25 MB"})
            continue
        if report.bytes + len(data) > IMPORT_MAX_TOTAL_BYTES:
            report.rejected.append({"path": shown, "reason": "se superó el límite total de 300 MB"})
            continue
        try:
            target_path = safe_path(root, shown)
        except WorkspaceError as exc:
            report.rejected.append({"path": shown, "reason": str(exc)})
            continue
        if target_path.is_dir():
            report.rejected.append({"path": shown, "reason": "ya existe una carpeta con ese nombre"})
            continue
        if target_path.exists() and not overwrite:
            report.skipped_existing.append(shown)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = target_path.with_name(f".{target_path.name}.wadima-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target_path)
        report.written.append(shown)
        report.bytes += len(data)
    return report


def zip_items(data: bytes) -> Iterator[tuple[str, bytes]]:
    """Entradas de un ZIP con límites contra bombas de compresión; sin enlaces simbólicos ni archivos cifrados."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise WorkspaceError("El archivo no es un ZIP válido") from exc
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > IMPORT_MAX_ENTRIES:
            raise WorkspaceError(f"El ZIP tiene más de {IMPORT_MAX_ENTRIES} archivos")
        if sum(i.file_size for i in infos) > IMPORT_MAX_TOTAL_BYTES:
            raise WorkspaceError("El contenido descomprimido supera 300 MB")
        for info in infos:
            if info.flag_bits & 0x1:
                raise WorkspaceError("El ZIP está cifrado")
            if stat.S_ISLNK(info.external_attr >> 16):
                continue  # enlaces simbólicos: se omiten
            if info.file_size > IMPORT_MAX_FILE_BYTES:
                yield info.filename, b"\0" * (IMPORT_MAX_FILE_BYTES + 1)  # se rechaza por tamaño sin leerlo
                continue
            with zf.open(info) as fh:
                content = fh.read(IMPORT_MAX_FILE_BYTES + 1)
            yield info.filename, content


def detect_kind(root: Path) -> str:
    """Tipo de proyecto a partir de su contenido (determina los perfiles de ejecución disponibles)."""
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            meta = json.loads(pkg.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            meta = {}
        deps = {**(meta.get("dependencies") or {}), **(meta.get("devDependencies") or {})}
        return "vite-vanilla" if "vite" in deps else "node"
    if any((root / f).is_file() for f in ("main.py", "requirements.txt", "pyproject.toml")) or any(root.glob("*.py")):
        return "python"
    if (root / "index.html").is_file():
        return "static"
    return "empty"


def package_scripts(root: Path) -> dict[str, str]:
    try:
        return dict(json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts") or {})
    except (ValueError, OSError):
        return {}
