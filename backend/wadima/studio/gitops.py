"""Operaciones Git por proyecto (argv explícito, sin shell, sin hooks, entorno saneado).

Git se ejecuta dentro del sandbox de Studio: el repositorio es escribible por el código del proyecto, así que
una configuración maliciosa en .git/config (fsmonitor, filtros, diff externo...) solo correría con sus mismos
límites, nunca con los permisos del servidor."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from wadima.config import get_settings
from wadima.studio import sandbox


class GitError(Exception):
    pass


def _git() -> str:
    path = shutil.which("git")
    if not path:
        raise GitError("Git no está instalado en el servidor")
    return path


def _env() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "LC_ALL": "C.UTF-8",
            "HOME": str(get_settings().studio_dir / ".githome"),
        }
    )
    return env


def run(root: Path, *args: str, author: tuple[str, str] | None = None, check: bool = True, timeout: int = 60) -> str:
    hooks = get_settings().studio_dir / ".nohooks"
    hooks.mkdir(parents=True, exist_ok=True)
    cmd = [_git(), "-c", f"core.hooksPath={hooks}", "-c", "core.autocrlf=false", "-c", "safe.directory=*"]
    if author:
        cmd += ["-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}"]
    cmd += list(args)
    try:
        res = sandbox.run_capture(cmd, root, _env(), timeout)
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} superó el tiempo límite") from exc
    out = res.stdout.decode("utf-8", errors="replace")
    if check and res.returncode != 0:
        raise GitError(res.stderr.decode("utf-8", errors="replace").strip() or f"git {args[0]} falló")
    return out


def init(root: Path, author: tuple[str, str], brand: str = "WADIMA") -> str:
    run(root, "init", "-b", "main")
    run(root, "add", "-A")
    run(root, "commit", "-m", f"Proyecto creado desde {brand}", author=author)
    return head(root)


def head(root: Path) -> str:
    return run(root, "rev-parse", "HEAD").strip()


def status(root: Path) -> list[dict]:
    out = run(root, "status", "--porcelain=v1", "-z")
    items = []
    for entry in filter(None, out.split("\x00")):
        code, path = entry[:2], entry[3:]
        items.append({"path": path, "index": code[0], "worktree": code[1]})
    return items


def branch(root: Path) -> str:
    return run(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def commit(root: Path, message: str, author: tuple[str, str]) -> str | None:
    run(root, "add", "-A")
    if not run(root, "status", "--porcelain").strip():
        return None
    run(root, "commit", "-m", message, author=author)
    return head(root)


def log(root: Path, path: str | None = None, limit: int = 50) -> list[dict]:
    args = ["log", f"-n{limit}", "--date=iso-strict", "--pretty=format:%H%x1f%an%x1f%ae%x1f%ad%x1f%s%x1e"]
    if path:
        args += ["--", path]
    out = run(root, *args)
    commits = []
    for rec in filter(None, (r.strip() for r in out.split("\x1e"))):
        h, an, ae, ad, s = rec.split("\x1f")
        commits.append({"hash": h, "author": an, "email": ae, "date": ad, "message": s})
    return commits


REF_RE = re.compile(r"^[A-Za-z0-9._/\-]{1,100}$")


def _ref(ref: str) -> str:
    if not REF_RE.match(ref) or ref.startswith("-") or ".." in ref:
        raise GitError("Referencia Git no válida")
    return ref


def diff(root: Path, path: str | None = None, ref_from: str | None = None, ref_to: str | None = None) -> str:
    args = ["diff", "--no-color"]
    if ref_from:
        args.append(_ref(ref_from))
    if ref_to:
        args.append(_ref(ref_to))
    if path:
        args += ["--", path]
    return run(root, *args)


def show(root: Path, ref: str, path: str) -> str:
    return run(root, "show", f"{_ref(ref)}:{path}")


def restore(root: Path, ref: str, path: str) -> None:
    run(root, "checkout", _ref(ref), "--", path)


def branches(root: Path) -> list[str]:
    out = run(root, "branch", "--format=%(refname:short)")
    return [b.strip() for b in out.splitlines() if b.strip()]


def create_branch(root: Path, name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-/]{0,60}", name):
        raise GitError("Nombre de rama no válido")
    run(root, "branch", name)


def checkout(root: Path, name: str) -> None:
    if status(root):
        raise GitError("Hay cambios sin confirmar: haga commit antes de cambiar de rama")
    run(root, "checkout", _ref(name))
