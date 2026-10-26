"""Aplicación FastAPI de WADIMA."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Route

from wadima import __version__, background
from wadima.api import (
    admin,
    ai,
    areas,
    auth,
    drive,
    library,
    meetings,
    memory,
    notifications,
    org,
    presence,
    projects,
    studio,
    timeclock,
    users,
)
from wadima.config import get_settings
from wadima.db import SessionLocal
from wadima.logs import setup_logging
from wadima.realtime import hub

log = logging.getLogger("wadima")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    hub.bind_loop(asyncio.get_running_loop())
    settings = get_settings()
    for d in (settings.data_dir, settings.blobs_dir, settings.studio_dir, settings.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    preview_task = None
    if not settings.is_test:
        asyncio.get_running_loop().run_in_executor(None, _harden_storage, settings)
        background.start()
        preview_task = asyncio.create_task(_serve_preview(settings), name="preview-server")
    log.info("WADIMA %s iniciado (env=%s)", __version__, settings.env)
    async with _mcp.lifespan():
        yield
    from wadima.studio import runner

    runner.stop_all()
    if preview_task:
        preview_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await preview_task
    await background.stop()


def _harden_storage(settings) -> None:
    """Etiqueta los datos del servidor como inaccesibles desde Project Studio (también se comprueba al ejecutar)."""
    from wadima.studio.integrity import ensure_layout

    try:
        ensure_layout(settings)
    except OSError:
        log.exception("No se pudieron aplicar las etiquetas de integridad; Studio no ejecutará código")


class _EmbeddedServer(uvicorn.Server):
    """Servidor uvicorn secundario dentro del mismo bucle: no instala manejadores de señales propios."""

    def install_signal_handlers(self) -> None:  # uvicorn < 0.29
        return None

    @contextlib.contextmanager
    def capture_signals(self):  # uvicorn >= 0.29
        yield


async def _serve_preview(settings) -> None:
    """Live Preview en un puerto propio (origen distinto de la aplicación)."""
    from wadima.studio.preview import bind_loop, preview_app

    bind_loop(asyncio.get_running_loop())
    config = uvicorn.Config(
        preview_app,
        host=settings.bind_host,
        port=settings.preview_port,
        log_level="warning",
        lifespan="off",
        ws="websockets",
        ssl_certfile=settings.tls_cert_file or None,
        ssl_keyfile=settings.tls_key_file or None,
    )
    try:
        await _EmbeddedServer(config).serve()
    except (OSError, SystemExit) as exc:
        log.error("No se pudo iniciar el servidor de vista previa en el puerto %s: %s", settings.preview_port, exc)


class _MCPEndpoint:
    """Ruta /mcp: delega en el servidor MCP oficial, que se crea de nuevo en cada arranque (su gestor de sesiones
    solo puede iniciarse una vez por instancia)."""

    def __init__(self) -> None:
        self.app = None

    @contextlib.asynccontextmanager
    async def lifespan(self):
        if not get_settings().mcp_enabled:
            yield
            return
        from wadima import mcp_server

        self.app = mcp_server.build_app()
        async with mcp_server.server.session_manager.run():
            yield
        self.app = None

    async def __call__(self, scope, receive, send):
        if self.app is None:
            response = JSONResponse({"detail": "Servidor MCP no disponible"}, status_code=503)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


_mcp = _MCPEndpoint()


def security_headers(path: str) -> dict[str, str]:
    s = get_settings()
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "X-Frame-Options": "SAMEORIGIN",
        # El micrófono solo se pide en la sala de reuniones y por acción del usuario.
        "Permissions-Policy": "camera=(), microphone=(self), geolocation=()",
    }
    if not path.startswith("/api/"):
        headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws: wss:; "
            f"frame-src 'self' {s.preview_public_origin}; worker-src 'self' blob:; object-src 'none'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
        )
    else:
        headers["Cache-Control"] = "no-store"
    return headers


def create_app() -> FastAPI:
    production = get_settings().env == "production"
    app = FastAPI(
        title="WADIMA",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if production else "/api/docs",
        openapi_url=None if production else "/api/openapi.json",
        redoc_url=None,
    )

    @app.middleware("http")
    async def _headers_and_timing(request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("Error no controlado en %s %s", request.method, request.url.path)
            response = JSONResponse({"detail": "Error interno del servidor"}, status_code=500)
        for k, v in security_headers(request.url.path).items():
            response.headers.setdefault(k, v)
        elapsed = (time.perf_counter() - start) * 1000
        if response.status_code >= 500:
            log.error("%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code, elapsed)
        return response

    @app.get("/api/health", tags=["health"])
    def health():
        db_ok, latency, error = True, None, None
        t0 = time.perf_counter()
        try:
            db = SessionLocal()
            try:
                db.execute(text("SELECT 1"))
            finally:
                db.close()
            latency = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as exc:  # noqa: BLE001
            db_ok, error = False, type(exc).__name__
        return JSONResponse(
            {
                "status": "ok" if db_ok else "degraded",
                "version": __version__,
                "database": db_ok,
                "db_latency_ms": latency,
                "error": error,
                "online_users": len(hub.online_users()),
            },
            status_code=200 if db_ok else 503,
        )

    modules = (auth, users, areas, presence, notifications, projects, library, memory, drive, ai, studio, admin)
    for module in (*modules, org, timeclock, meetings):
        app.include_router(module.router)
    app.router.routes.append(Route("/mcp", endpoint=_mcp))
    app.add_api_websocket_route("/ws", presence.websocket_endpoint)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    dist = get_settings().frontend_dist

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "ws")):
            raise StarletteHTTPException(404)
        if not dist.exists():
            return JSONResponse({"detail": "Frontend no compilado. Ejecute: python manage.py build"}, 404)
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in f"/{full_path}" else {}
            return FileResponse(candidate, headers=headers)
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})


app = create_app()
