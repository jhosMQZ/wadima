#!/usr/bin/env python3
"""Tareas reproducibles de WADIMA (solo biblioteca estándar).

Uso:  python manage.py <comando>

  setup              Instala dependencias, prepara PostgreSQL, migra y crea los 3 usuarios.
  db init|start|stop|status|backup
  migrate            Aplica las migraciones de Alembic.
  users              Crea las tres cuentas iniciales (si no existen).
  dev                Arranca PostgreSQL, backend (8000) y frontend Vite (5173).
  start              Modo producción local: backend sirviendo el frontend compilado.
  build              Compila el frontend.
  tls <ip|nombre>... Certificados HTTPS para la red privada (micrófono en reuniones desde otros equipos).
  test [--e2e]       Lint, tipos y pruebas (backend y frontend; --e2e añade Playwright).
"""

from __future__ import annotations

import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
ENV_FILE = ROOT / ".env"
HOME_DIR = Path(os.environ.get("WADIMA_HOME", Path.home() / ".wadima"))
PG_BIN = Path(os.environ.get("WADIMA_PG_BIN", HOME_DIR / "tools" / "pgsql" / "bin"))
PG_DATA = Path(os.environ.get("WADIMA_PG_DATA", HOME_DIR / "pgdata"))
PG_PORT = int(os.environ.get("WADIMA_PG_PORT", "5433"))
PG_LOG = HOME_DIR / "postgres.log"
EXE = ".exe" if os.name == "nt" else ""


def log(msg: str) -> None:
    print(f"[wadima] {msg}", flush=True)


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None, check: bool = True):
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, env=env, check=check)


def tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        sys.exit(f"No se encontró '{name}' en PATH.")
    return found


# --------------------------------------------------------------------------- .env
def read_env() -> dict[str, str]:
    data: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    return data


def write_env(updates: dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- PostgreSQL
def pg(name: str) -> str:
    path = PG_BIN / f"{name}{EXE}"
    if path.exists():
        return str(path)
    return tool(name)


def pg_running() -> bool:
    if not PG_DATA.exists():
        return False
    res = subprocess.run([pg("pg_ctl"), "-D", str(PG_DATA), "status"], capture_output=True, text=True)
    return res.returncode == 0


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def db_start() -> None:
    if pg_running():
        log(f"PostgreSQL ya está en ejecución (puerto {PG_PORT}).")
        return
    PG_LOG.parent.mkdir(parents=True, exist_ok=True)
    log("Iniciando PostgreSQL...")
    # Sin heredar stdin/stdout: postgres sigue vivo tras pg_ctl y no debe retener tuberías del llamador.
    res = subprocess.run(
        [pg("pg_ctl"), "-D", str(PG_DATA), "-l", str(PG_LOG), "-w", "-t", "60", "start"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if res.returncode != 0:
        sys.exit(f"No se pudo iniciar PostgreSQL; revise {PG_LOG}")
    log(f"PostgreSQL en ejecución (puerto {PG_PORT}).")


def db_stop() -> None:
    if pg_running():
        run([pg("pg_ctl"), "-D", str(PG_DATA), "-m", "fast", "-w", "stop"])
    else:
        log("PostgreSQL no está en ejecución.")


def psql_super(sql: str, db: str = "postgres") -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PGPASSWORD"] = (HOME_DIR / "secrets" / "pg_superuser").read_text().strip()
    return subprocess.run(
        [pg("psql"), "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", "postgres", "-d", db,
         "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        env=env, capture_output=True, text=True,
    )


def db_init() -> None:
    secrets_dir = HOME_DIR / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    super_file = secrets_dir / "pg_superuser"
    if not PG_DATA.exists():
        super_pw = secrets.token_urlsafe(24)
        super_file.write_text(super_pw, encoding="utf-8")
        pwfile = secrets_dir / "initdb.tmp"
        pwfile.write_text(super_pw, encoding="utf-8")
        try:
            run([pg("initdb"), "-D", str(PG_DATA), "-U", "postgres", "--pwfile", str(pwfile),
                 "-E", "UTF8", "--locale=C", "--auth-local=scram-sha-256", "--auth-host=scram-sha-256"])
        finally:
            pwfile.unlink(missing_ok=True)
        conf = PG_DATA / "postgresql.conf"
        with conf.open("a", encoding="utf-8") as fh:
            fh.write(
                "\n# --- WADIMA ---\n"
                "listen_addresses = '127.0.0.1'\n"
                f"port = {PG_PORT}\n"
                "shared_buffers = 64MB\n"
                "max_connections = 40\n"
                "timezone = 'UTC'\n"
                "log_timezone = 'UTC'\n"
            )
    db_start()
    env = read_env()
    owner_pw = env.get("WADIMA_DB_PASSWORD") or secrets.token_urlsafe(24)
    app_pw = env.get("WADIMA_APP_DB_PASSWORD") or secrets.token_urlsafe(24)
    # wadima: propietario de las tablas (migraciones, copias). wadima_app: la aplicación, sin propiedad, de modo
    # que no puede desactivar los triggers que protegen la auditoría y los registros horarios.
    for role, pw in (("wadima", owner_pw), ("wadima_app", app_pw)):
        exists = psql_super(f"SELECT 1 FROM pg_roles WHERE rolname='{role}'").stdout.strip()
        verb = "ALTER" if exists == "1" else "CREATE"
        res = psql_super(f"{verb} ROLE {role} {'LOGIN ' if verb == 'CREATE' else ''}PASSWORD '{pw}'")
        if res.returncode:
            sys.exit(res.stderr)
    for db in ("wadima", "wadima_test", "wadima_e2e"):
        if psql_super(f"SELECT 1 FROM pg_database WHERE datname='{db}'").stdout.strip() != "1":
            res = psql_super(f"CREATE DATABASE {db} OWNER wadima ENCODING 'UTF8'")
            if res.returncode:
                sys.exit(res.stderr)
            log(f"Base de datos {db} creada.")
    owner = f"postgresql+psycopg://wadima:{owner_pw}@127.0.0.1:{PG_PORT}"
    app = f"postgresql+psycopg://wadima_app:{app_pw}@127.0.0.1:{PG_PORT}"
    write_env({
        "WADIMA_DB_PASSWORD": owner_pw,
        "WADIMA_APP_DB_PASSWORD": app_pw,
        "WADIMA_DATABASE_URL": f"{app}/wadima",
        "WADIMA_MIGRATION_DATABASE_URL": f"{owner}/wadima",
        "WADIMA_TEST_DATABASE_URL": f"{owner}/wadima_test",
        "WADIMA_TEST_APP_DATABASE_URL": f"{app}/wadima_test",
        "WADIMA_E2E_DATABASE_URL": f"{owner}/wadima_e2e",
        "WADIMA_E2E_APP_DATABASE_URL": f"{app}/wadima_e2e",
    })
    log("PostgreSQL listo y .env actualizado (credenciales solo en .env local, ignorado por Git).")
    log("Aplique las migraciones (python manage.py migrate) para conceder permisos al rol wadima_app.")


def db_backup() -> None:
    env = read_env()
    out_dir = ROOT / "var" / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"wadima-{stamp}.dump"
    penv = os.environ.copy()
    penv["PGPASSWORD"] = env["WADIMA_DB_PASSWORD"]
    run([pg("pg_dump"), "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", "wadima", "-Fc", "-f", str(target),
         "wadima"], env=penv)
    log(f"Copia local creada: {target}")


# --------------------------------------------------------------------------- tareas
def uv(*args: str, check: bool = True):
    return run([tool("uv"), "run", "--project", str(BACKEND), *args], cwd=BACKEND, check=check)


def pnpm(*args: str, check: bool = True):
    return run([tool("pnpm"), *args], cwd=FRONTEND, check=check)


def cmd_setup() -> None:
    run([tool("uv"), "sync"], cwd=BACKEND)
    pnpm("install")
    db_init()
    cmd_migrate()
    uv("python", "-m", "wadima.cli", "bootstrap-users")


def cmd_migrate() -> None:
    uv("alembic", "upgrade", "head")


def cmd_build() -> None:
    pnpm("run", "build")


def spawn(cmd: list[str], cwd: Path) -> subprocess.Popen:
    log("$ " + " ".join(cmd))
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(cmd, cwd=cwd, creationflags=flags)


def wait_all(procs: list[subprocess.Popen]) -> None:
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                if os.name == "nt":
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def uvicorn_cmd(env: dict[str, str]) -> tuple[list[str], str]:
    host = env.get("WADIMA_BIND_HOST", "127.0.0.1")
    cmd = [tool("uv"), "run", "uvicorn", "wadima.main:app", "--host", host, "--port", "8000"]
    cert, key = env.get("WADIMA_TLS_CERT_FILE"), env.get("WADIMA_TLS_KEY_FILE")
    if cert and key:
        cmd += ["--ssl-certfile", cert, "--ssl-keyfile", key]
    return cmd, "https" if cert and key else "http"


def cmd_dev() -> None:
    db_start()
    cmd, scheme = uvicorn_cmd(read_env())
    os.environ["WADIMA_API"] = f"{scheme}://127.0.0.1:8000"  # destino del proxy de Vite
    procs = [spawn(cmd, BACKEND), spawn([tool("pnpm"), "run", "dev"], FRONTEND)]
    log(f"Frontend: http://127.0.0.1:5173  ·  API: {scheme}://127.0.0.1:8000/api/health")
    wait_all(procs)


def cmd_start() -> None:
    db_start()
    if not (FRONTEND / "dist" / "index.html").exists():
        cmd_build()
    env = read_env()
    cmd, scheme = uvicorn_cmd(env)
    procs = [spawn(cmd, BACKEND)]
    log(f"WADIMA: {scheme}://{env.get('WADIMA_BIND_HOST', '127.0.0.1')}:8000")
    wait_all(procs)


def cmd_tls(hosts: list[str]) -> None:
    """Genera la cadena de certificados y configura .env para servir por HTTPS con esos nombres.
    No cambia la interfaz de escucha (WADIMA_BIND_HOST): abrir WADIMA a la red es una decisión explícita."""
    if not hosts:
        sys.exit("Uso: python manage.py tls <ip-privada-o-nombre> [...]")
    out = subprocess.run(
        [tool("uv"), "run", "python", "-m", "wadima.cli", "tls", *hosts],
        cwd=BACKEND, capture_output=True, text=True, check=True,
    ).stdout
    paths = dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)
    main_host = hosts[0]
    origins = [f"https://{h}:8000" for h in hosts] + ["https://127.0.0.1:8000", "https://localhost:8000",
                                                       "http://127.0.0.1:5173", "http://localhost:5173"]
    write_env({
        "WADIMA_TLS_CERT_FILE": paths["cert"],
        "WADIMA_TLS_KEY_FILE": paths["key"],
        "WADIMA_COOKIE_SECURE": "true",
        "WADIMA_PUBLIC_ORIGIN": f"https://{main_host}:8000",
        "WADIMA_PREVIEW_PUBLIC_ORIGIN": f"https://{main_host}:8001",
        "WADIMA_ALLOWED_ORIGINS": "[" + ",".join(f'"{o}"' for o in dict.fromkeys(origins)) + "]",
    })
    log(f"Certificados en {Path(paths['cert']).parent}")
    log(f"Entregue a cada socio la autoridad local (pública): {paths['ca_cert']}")
    log("Para escuchar en la red privada, añada WADIMA_BIND_HOST=<ip-privada> a .env y reinicie WADIMA.")


def cmd_test(e2e: bool) -> None:
    failures = []
    steps = [
        ("ruff", lambda: uv("ruff", "check", ".", check=False)),
        ("pytest", lambda: uv("pytest", check=False)),
        ("eslint", lambda: pnpm("run", "lint", check=False)),
        ("typecheck", lambda: pnpm("run", "typecheck", check=False)),
        ("vitest", lambda: pnpm("run", "test", check=False)),
    ]
    if e2e:
        steps.append(("playwright", lambda: pnpm("run", "e2e", check=False)))
    for name, fn in steps:
        if fn().returncode != 0:
            failures.append(name)
    if failures:
        sys.exit(f"Fallaron: {', '.join(failures)}")
    log("Todas las comprobaciones pasaron.")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "setup":
        cmd_setup()
    elif cmd == "db":
        sub = args[1] if len(args) > 1 else "status"
        if sub == "init":
            db_init()
        elif sub == "start":
            db_start()
        elif sub == "stop":
            db_stop()
        elif sub == "backup":
            db_backup()
        else:
            log(f"PostgreSQL {'en ejecución' if pg_running() else 'detenido'} · puerto {PG_PORT} · {PG_DATA}")
    elif cmd == "migrate":
        cmd_migrate()
    elif cmd == "users":
        uv("python", "-m", "wadima.cli", "bootstrap-users")
    elif cmd == "dev":
        cmd_dev()
    elif cmd == "start":
        cmd_start()
    elif cmd == "build":
        cmd_build()
    elif cmd == "tls":
        cmd_tls(args[1:])
    elif cmd == "test":
        cmd_test("--e2e" in args)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
