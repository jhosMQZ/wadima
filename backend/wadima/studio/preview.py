"""Servidor de Live Preview (puerto propio = origen distinto de la aplicación).

Publica /p/<proyecto>/... solo para miembros del área del proyecto (sesión de WADIMA). Si hay un servidor
de desarrollo Vite en marcha, hace de proxy HTTP y WebSocket (HMR) hacia 127.0.0.1; para proyectos estáticos
sirve los archivos e inyecta un pequeño script de recarga. El código de la vista previa no puede leer la
cookie httpOnly ni el token CSRF de la aplicación porque se ejecuta en otro origen.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
from collections import defaultdict

import httpx
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from wadima.config import get_settings
from wadima.db import SessionLocal
from wadima.models import StudioProject
from wadima.security.auth import COOKIE, resolve_session
from wadima.security.perms import area_role
from wadima.studio import runner
from wadima.studio.workspace import WorkspaceError, project_root, safe_path

log = logging.getLogger("wadima.preview")

HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "cookie",
}

RELOAD_SNIPPET = (
    "<script>(()=>{const u=(location.protocol==='https:'?'wss':'ws')+'://'+location.host"
    "+'/__wadima/reload/%s';let ws;const c=()=>{ws=new WebSocket(u);ws.onmessage=()=>location.reload();"
    "ws.onclose=()=>setTimeout(c,2000)};c();})();</script>"
)

_reload_listeners: dict[str, set[asyncio.Queue]] = defaultdict(set)
_loop: asyncio.AbstractEventLoop | None = None


def notify_reload(slug: str) -> None:
    """Llamado al guardar archivos (desde hilos) para recargar las vistas previas estáticas."""
    if _loop is None:
        return
    for q in list(_reload_listeners.get(slug, ())):
        _loop.call_soon_threadsafe(q.put_nowait, "reload")


def _authorize(token: str | None, slug: str) -> tuple[StudioProject | None, int]:
    db = SessionLocal()
    try:
        principal = resolve_session(db, token)
        if principal is None or principal.user.must_change_password:
            return None, 401
        sp = db.execute(select(StudioProject).where(StudioProject.slug == slug)).scalar_one_or_none()
        if sp is None or sp.archived_at is not None:
            return None, 404
        if area_role(db, principal, sp.area_id) is None:
            return None, 404
        db.expunge(sp)
        return sp, 200
    finally:
        db.close()


def _frame_headers() -> dict[str, str]:
    s = get_settings()
    return {
        "Content-Security-Policy": f"frame-ancestors 'self' {' '.join(s.allowed_origins)}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }


def _page(title: str, body: str, status: int) -> HTMLResponse:
    html = (
        f"<!doctype html><meta charset=utf-8><title>{title}</title>"
        f"<body style='font-family:system-ui;background:#12152b;color:#e8eaf6;padding:2rem'>"
        f"<h2>{title}</h2><p>{body}</p></body>"
    )
    return HTMLResponse(html, status_code=status, headers=_frame_headers())


async def preview_http(request: Request) -> Response:
    slug = request.path_params["slug"]
    rest = request.path_params.get("path", "")
    sp, code = await run_in_threadpool(_authorize, request.cookies.get(COOKIE), slug)
    if sp is None:
        if code == 401:
            return _page(
                "Inicie sesión en WADIMA", "La vista previa solo está disponible para socios autenticados.", 401
            )
        return _page("Vista previa no encontrada", "El proyecto no existe o no tiene acceso a su área.", 404)
    active = runner.dev_server(slug)
    if active is not None:
        return await _proxy(request, active.port, slug, rest)
    if sp.template == "static":
        return await run_in_threadpool(_serve_static, slug, rest)
    return _page(
        "Servidor de desarrollo detenido",
        "Inicie el servidor «dev» desde Project Studio para ver la vista previa en vivo.",
        503,
    )


async def _proxy(request: Request, port: int, slug: str, rest: str) -> Response:
    url = f"http://127.0.0.1:{port}/p/{slug}/{rest}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers["host"] = f"127.0.0.1:{port}"
    headers.pop("origin", None)
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0), trust_env=False)
    try:
        upstream = await client.send(
            client.build_request(request.method, url, headers=headers, content=await request.body()), stream=True
        )
    except httpx.HTTPError:
        await client.aclose()
        return _page("Servidor de desarrollo no disponible", "El proceso está arrancando o se detuvo.", 502)
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_HEADERS}
    out_headers.update(_frame_headers())

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(body(), status_code=upstream.status_code, headers=out_headers)


def _serve_static(slug: str, rest: str) -> Response:
    root = project_root(slug)
    try:
        target = safe_path(root, rest or "index.html", allow_root=True)
    except WorkspaceError:
        return _page("Ruta no permitida", "", 400)
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        return _page("No encontrado", rest, 404)
    data = target.read_bytes()
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if mime == "text/html":
        snippet = (RELOAD_SNIPPET % slug).encode()
        data = data.replace(b"</body>", snippet + b"</body>") if b"</body>" in data else data + snippet
    return Response(data, media_type=mime, headers=_frame_headers())


def _origin_ok(origin: str | None) -> bool:
    """Solo la propia vista previa o la aplicación pueden abrir WebSockets autenticados con la cookie."""
    if not origin:
        return True
    s = get_settings()
    return origin.rstrip("/") in {s.preview_public_origin.rstrip("/"), *s.allowed_origins}


async def preview_ws(websocket: WebSocket) -> None:
    slug = websocket.path_params["slug"]
    rest = websocket.path_params.get("path", "")
    if not _origin_ok(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return
    sp, _ = await run_in_threadpool(_authorize, websocket.cookies.get(COOKIE), slug)
    active = runner.dev_server(slug) if sp else None
    if sp is None or active is None:
        await websocket.close(code=4404)
        return
    from websockets.asyncio.client import connect
    from websockets.typing import Subprotocol

    protocols = [p.strip() for p in websocket.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    query = f"?{websocket.url.query}" if websocket.url.query else ""
    uri = f"ws://127.0.0.1:{active.port}/p/{slug}/{rest}{query}"
    try:
        upstream = await connect(
            uri, subprotocols=[Subprotocol(p) for p in protocols] or None, proxy=None, open_timeout=10
        )
    except Exception:  # noqa: BLE001
        await websocket.close(code=1011)
        return
    await websocket.accept(subprotocol=upstream.subprotocol)

    async def client_to_upstream():
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("text") is not None:
                    await upstream.send(msg["text"])
                elif msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
        finally:
            await upstream.close()

    async def upstream_to_client():
        try:
            async for data in upstream:
                if isinstance(data, str):
                    await websocket.send_text(data)
                else:
                    await websocket.send_bytes(data)
        finally:
            with contextlib.suppress(Exception):
                await websocket.close()

    tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
    _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()


async def reload_ws(websocket: WebSocket) -> None:
    slug = websocket.path_params["slug"]
    if not _origin_ok(websocket.headers.get("origin")):
        await websocket.close(code=4403)
        return
    sp, _ = await run_in_threadpool(_authorize, websocket.cookies.get(COOKIE), slug)
    if sp is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue()
    _reload_listeners[slug].add(q)
    try:
        receiver = asyncio.create_task(websocket.receive())
        while True:
            getter = asyncio.create_task(q.get())
            done, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                getter.cancel()
                break
            await websocket.send_text("reload")
    except WebSocketDisconnect:
        pass
    finally:
        _reload_listeners[slug].discard(q)


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


@contextlib.asynccontextmanager
async def _lifespan(_app):
    bind_loop(asyncio.get_running_loop())
    yield


def create_preview_app() -> Starlette:
    return Starlette(
        routes=[
            WebSocketRoute("/__wadima/reload/{slug}", reload_ws),
            Route("/p/{slug}/{path:path}", preview_http, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]),
            WebSocketRoute("/p/{slug}/{path:path}", preview_ws),
        ],
        lifespan=_lifespan,
    )


preview_app = create_preview_app()
