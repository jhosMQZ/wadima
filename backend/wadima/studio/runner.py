"""Gestor de ejecuciones de Project Studio: instalación, servidor de desarrollo (Vite), compilación, pruebas
y consola restringida. Cada ejecución corre dentro del sandbox con límites y su salida se transmite en vivo."""

from __future__ import annotations

import logging
import shlex
import socket
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from wadima.activity import log_activity
from wadima.config import get_settings
from wadima.db import SessionLocal
from wadima.models import StudioProject, StudioRun
from wadima.realtime import area_audience, hub
from wadima.security.auth import now_utc
from wadima.studio import sandbox
from wadima.studio.workspace import package_scripts, project_root

log = logging.getLogger("wadima.studio")

LONG_RUNNING = {"dev"}
CONSOLE_PROGRAMS = {"node", "npm", "pnpm", "python", "git"}
SHELL_OPERATORS = {"&&", "||", "|", "&", ";", ">", ">>", "<", "2>", "2>&1"}
GIT_READONLY = {"status", "log", "diff", "show", "branch", "rev-parse", "ls-files"}
MAX_LINES = 2000


class RunError(Exception):
    pass


@dataclass
class ActiveRun:
    run_id: uuid.UUID
    project_id: uuid.UUID
    slug: str
    area_id: uuid.UUID
    profile: str
    argv: list[str]
    proc: object
    job: sandbox.JobHandle | None
    port: int | None = None
    lines: deque = field(default_factory=lambda: deque(maxlen=MAX_LINES))
    pending: list[str] = field(default_factory=list)
    status: str = "running"
    ready: bool = False
    exit_code: int | None = None
    started: float = field(default_factory=time.time)
    stopped_by_user: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


RUNS: dict[uuid.UUID, ActiveRun] = {}
DEV_SERVERS: dict[str, uuid.UUID] = {}
_lock = threading.Lock()


def toolchains() -> dict[str, bool]:
    import shutil

    names = [
        "node",
        "npm",
        "pnpm",
        "python",
        "git",
        "java",
        "javac",
        "go",
        "rustc",
        "cargo",
        "gcc",
        "g++",
        "dotnet",
        "php",
        "docker",
        "podman",
    ]
    return {n: bool(shutil.which(n)) for n in names}


def _free_port() -> int:
    s = get_settings()
    used = {r.port for r in RUNS.values() if r.port}
    for port in range(s.studio_port_range_start, s.studio_port_range_end + 1):
        if port in used:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RunError("No quedan puertos libres para servidores de vista previa")


def _exe(name: str) -> str:
    path = sandbox.resolve_executable(name)
    if not path:
        raise RunError(f"'{name}' no está instalado en el servidor")
    return path


def _package_manager() -> tuple[str, list[str]]:
    if sandbox.resolve_executable("pnpm"):
        return _exe("pnpm"), ["install", "--no-frozen-lockfile", "--reporter=append-only"]
    return _exe("npm"), ["install", "--no-audit", "--no-fund"]


def build_argv(template: str, profile: str, root: Path, port: int | None, console: str | None) -> list[str]:
    if profile == "console":
        if not console:
            raise RunError("Comando vacío")
        try:
            parts = shlex.split(console, posix=True)
        except ValueError as exc:
            raise RunError(f"Comando no válido: {exc}") from exc
        prog = parts[0].lower()
        if prog not in CONSOLE_PROGRAMS:
            raise RunError(f"Programa no permitido en la consola. Permitidos: {', '.join(sorted(CONSOLE_PROGRAMS))}")
        if prog == "git" and (len(parts) < 2 or parts[1] not in GIT_READONLY):
            raise RunError("En la consola solo se permiten comandos de Git de lectura (use el panel Git para commits)")
        # Sin intérprete de comandos: un operador suelto sería un argumento literal; se rechaza para evitar confusión.
        if any(tok in SHELL_OPERATORS or tok.startswith(("$(", "`")) for tok in parts):
            raise RunError("No se admiten operadores de shell: los comandos se ejecutan sin intérprete")
        return [_exe(prog), *parts[1:]]
    if template == "vite-vanilla":
        if profile == "install":
            pm, args = _package_manager()
            return [pm, *args]
        if profile == "test":
            return [_exe("node"), "--test"]
        if not (root / "node_modules").exists():
            raise RunError("Instale primero las dependencias (perfil «install»)")
        vite = root / "node_modules" / ".bin" / ("vite.cmd" if sandbox.IS_WINDOWS else "vite")
        if profile == "dev":
            # --base: la vista previa se publica en /p/<proyecto>/ (también en proyectos importados sin la
            # configuración de la plantilla).
            base = f"/p/{root.name}/"
            return [str(vite), "--port", str(port), "--strictPort", "--host", "127.0.0.1", "--base", base]
        if profile == "build":
            return [str(vite), "build"]
    if template == "node":
        if profile == "install":
            pm, args = _package_manager()
            return [pm, *args]
        scripts = package_scripts(root)
        if profile in ("build", "test"):
            if profile not in scripts:
                raise RunError(f"package.json no define el script «{profile}»")
            pm, _args = _package_manager()
            return [pm, "run", profile]
    if template == "python":
        if profile == "run":
            if not (root / "main.py").is_file():
                raise RunError("No hay main.py en la raíz del proyecto; use la consola (python <archivo>.py)")
            return [_exe("python"), "main.py"]
        if profile == "test":
            return [_exe("python"), "-m", "unittest", "-v"]
    raise RunError(f"El perfil «{profile}» no está disponible para la plantilla «{template}»")


PROFILES = {
    "vite-vanilla": ["install", "dev", "build", "test", "console"],
    "node": ["install", "build", "test", "console"],
    "python": ["run", "test", "console"],
    "static": ["console"],
    "empty": ["console"],
}


def start(sp: StudioProject, profile: str, user, console: str | None = None) -> StudioRun:
    root = project_root(sp.slug)
    if profile not in PROFILES.get(sp.template, []):
        raise RunError(f"Perfil «{profile}» no disponible para esta plantilla")
    if profile == "dev":
        stop_dev(sp.slug)
    port = _free_port() if profile == "dev" else None
    argv = build_argv(sp.template, profile, root, port, console)
    s = get_settings()
    extra = {"WADIMA_BASE": f"/p/{sp.slug}/"} if profile == "dev" else {}
    env = sandbox.clean_env(root, extra)
    limits = sandbox.Limits(s.run_memory_mb, s.run_cpu_percent, s.run_max_processes)
    db = SessionLocal()
    try:
        run_row = StudioRun(
            studio_project_id=sp.id,
            user_id=user.id,
            profile=profile,
            command=_display(argv, root),
            status="running",
            port=port,
        )
        db.add(run_row)
        db.commit()
        try:
            proc, job = sandbox.spawn(argv, root, env, limits)
        except OSError as exc:
            run_row.status = "error"
            run_row.log_tail = f"No se pudo iniciar el proceso: {exc}"
            run_row.finished_at = now_utc()
            db.commit()
            raise RunError(run_row.log_tail) from exc
        run_row.pid = proc.pid
        db.commit()
        active = ActiveRun(
            run_id=run_row.id,
            project_id=sp.id,
            slug=sp.slug,
            area_id=sp.area_id,
            profile=profile,
            argv=argv,
            proc=proc,
            job=job,
            port=port,
        )
        active.lines.append(f"$ {' '.join(run_row.command)}")
        if job is not None:
            active.lines.append(
                f"[sandbox] Job Object: memoria ≤ {s.run_memory_mb} MB, CPU ≤ {s.run_cpu_percent}%"
                f"{'' if job.cpu_limited else ' (tope de CPU no disponible)'}, procesos ≤ "
                f"{s.run_max_processes}, integridad Baja (sin acceso a los datos del servidor), entorno sin secretos"
            )
        with _lock:
            RUNS[active.run_id] = active
            if profile == "dev":
                DEV_SERVERS[sp.slug] = active.run_id
        audience = area_audience(db, sp.area_id)
        threading.Thread(
            target=_reader, args=(active, audience), daemon=True, name=f"studio-out-{active.run_id}"
        ).start()
        threading.Thread(
            target=_waiter, args=(active, audience, user.id), daemon=True, name=f"studio-wait-{active.run_id}"
        ).start()
        _publish_state(active, audience)
        db.refresh(run_row)
        return run_row
    finally:
        db.close()


def _display(argv: list[str], root: Path) -> list[str]:
    out = []
    for a in argv:
        p = Path(a)
        out.append(p.name if p.is_absolute() else a)
    return out


def _reader(active: ActiveRun, audience) -> None:
    proc = active.proc
    last_flush = time.time()
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        with active.lock:
            active.lines.append(line)
            active.pending.append(line)
        if active.profile == "dev" and not active.ready and ("Local:" in line or "ready in" in line):
            active.ready = True
            _publish_state(active, audience)
        if time.time() - last_flush > 0.25:
            _flush(active, audience)
            last_flush = time.time()
    _flush(active, audience)


def _flush(active: ActiveRun, audience) -> None:
    with active.lock:
        lines, active.pending = active.pending, []
    if lines:
        hub.publish(
            {
                "type": "studio.log",
                "run_id": str(active.run_id),
                "project_id": str(active.project_id),
                "lines": lines[-500:],
            },
            audience,
        )


def _publish_state(active: ActiveRun, audience) -> None:
    hub.publish(
        {
            "type": "studio.run",
            "run_id": str(active.run_id),
            "project_id": str(active.project_id),
            "status": active.status,
            "ready": active.ready,
            "profile": active.profile,
        },
        audience,
    )


def _waiter(active: ActiveRun, audience, user_id: uuid.UUID) -> None:
    timeout = None if active.profile in LONG_RUNNING else get_settings().run_timeout_seconds
    proc = active.proc
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except Exception:  # noqa: BLE001 - TimeoutExpired
        timed_out = True
        if active.job:
            active.job.terminate()
        else:
            proc.kill()
        proc.wait(timeout=10)
    peak = active.job.peak_memory_mb() if active.job else None
    if active.job:
        active.job.close()
    active.exit_code = proc.returncode
    if active.stopped_by_user:
        active.status = "stopped"
    elif timed_out:
        active.status = "timeout"
        active.lines.append(f"[sandbox] Tiempo máximo agotado ({timeout}s): proceso detenido")
    else:
        active.status = "succeeded" if proc.returncode == 0 else "failed"
    if peak:
        active.lines.append(f"[sandbox] Memoria máxima usada: {peak} MB · código de salida {proc.returncode}")
    _flush(active, audience)
    with _lock:
        if DEV_SERVERS.get(active.slug) == active.run_id:
            DEV_SERVERS.pop(active.slug, None)
    db = SessionLocal()
    try:
        row = db.get(StudioRun, active.run_id)
        if row is not None:
            row.status = active.status
            row.exit_code = active.exit_code
            row.log_tail = "\n".join(list(active.lines)[-300:])
            row.finished_at = now_utc()
        sp = db.get(StudioProject, active.project_id)
        if sp is not None and active.profile in ("build", "test", "run"):
            from wadima.models import User

            user = db.get(User, user_id)
            verdict = {
                "succeeded": "correcto",
                "failed": "con errores",
                "timeout": "agotó el tiempo",
                "stopped": "detenido",
            }[active.status]
            kind = "studio.test" if active.profile == "test" else f"studio.{active.profile}"
            log_activity(
                db,
                kind if active.status == "succeeded" else "error.studio",
                f"{active.profile} de «{sp.name}»: {verdict}",
                user=user,
                area_id=sp.area_id,
                project_id=sp.project_id,
                ref_type="studio",
                ref_id=sp.id,
            )
        db.commit()
    finally:
        db.close()
    _publish_state(active, audience)
    # Se conserva el registro un tiempo para consultas; luego se libera memoria.
    threading.Timer(600, lambda: RUNS.pop(active.run_id, None)).start()


def stop(run_id: uuid.UUID) -> bool:
    active = RUNS.get(run_id)
    if active is None or active.status != "running":
        return False
    active.stopped_by_user = True
    if active.job:
        active.job.terminate()
    else:
        active.proc.kill()
    return True


def stop_dev(slug: str) -> None:
    run_id = DEV_SERVERS.get(slug)
    if run_id:
        stop(run_id)
        deadline = time.time() + 10
        while slug in DEV_SERVERS and time.time() < deadline:
            time.sleep(0.1)


def dev_server(slug: str) -> ActiveRun | None:
    run_id = DEV_SERVERS.get(slug)
    active = RUNS.get(run_id) if run_id else None
    return active if active and active.status == "running" else None


def stop_all() -> None:
    for run_id in list(RUNS):
        stop(run_id)


def snapshot(run_id: uuid.UUID) -> dict | None:
    active = RUNS.get(run_id)
    if active is None:
        return None
    with active.lock:
        lines = list(active.lines)
    return {
        "status": active.status,
        "ready": active.ready,
        "exit_code": active.exit_code,
        "port": active.port,
        "lines": lines,
        "profile": active.profile,
    }
