"""Hub de WebSockets: presencia en línea y difusión de eventos a usuarios autorizados.

Los eventos son señales pequeñas (tipo + IDs); los clientes vuelven a consultar los datos por REST,
donde se aplican los permisos. Los eventos generados dentro de una transacción se publican solo
después del commit (ver `emit`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket
from sqlalchemy import event, select
from sqlalchemy.orm import Session

log = logging.getLogger("wadima.realtime")


@dataclass(eq=False)
class Connection:
    ws: WebSocket
    user_id: uuid.UUID
    session_id: uuid.UUID
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=500))


class Hub:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self._conns: dict[uuid.UUID, set[Connection]] = defaultdict(set)
        self._lock = threading.Lock()
        self.on_presence_change: list = []  # callbacks(user_id, online: bool)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    # ------------------------------------------------------------------ conexiones
    def add(self, conn: Connection) -> bool:
        with self._lock:
            first = not self._conns[conn.user_id]
            self._conns[conn.user_id].add(conn)
        return first

    def remove(self, conn: Connection) -> bool:
        with self._lock:
            conns = self._conns.get(conn.user_id, set())
            conns.discard(conn)
            last = not conns
            if last:
                self._conns.pop(conn.user_id, None)
        return last

    def online_users(self) -> set[uuid.UUID]:
        with self._lock:
            return {uid for uid, conns in self._conns.items() if conns}

    def is_online(self, user_id: uuid.UUID) -> bool:
        with self._lock:
            return bool(self._conns.get(user_id))

    def close_session(self, session_id: uuid.UUID) -> None:
        """Cierra los sockets de una sesión revocada."""
        with self._lock:
            targets = [c for conns in self._conns.values() for c in conns if c.session_id == session_id]
        for conn in targets:
            self._put(conn, {"type": "session.revoked"})

    # ------------------------------------------------------------------ difusión
    def _put(self, conn: Connection, message: dict[str, Any]) -> None:
        try:
            conn.queue.put_nowait(message)
        except asyncio.QueueFull:
            log.warning("Cola llena para %s; se descarta un evento", conn.user_id)

    def _deliver(self, message: dict[str, Any], audience: set[uuid.UUID] | None) -> None:
        with self._lock:
            targets = [c for uid, conns in self._conns.items() if audience is None or uid in audience for c in conns]
        for conn in targets:
            self._put(conn, message)

    def publish(self, message: dict[str, Any], audience: set[uuid.UUID] | None = None) -> None:
        """Seguro para llamar desde hilos (endpoints síncronos, workers)."""
        message.setdefault("ts", datetime.now(UTC).isoformat())
        if self.loop is None or self.loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._deliver(message, audience)
        else:
            with contextlib.suppress(RuntimeError):
                self.loop.call_soon_threadsafe(self._deliver, message, audience)

    async def pump(self, conn: Connection) -> None:
        while True:
            message = await conn.queue.get()
            await conn.ws.send_text(json.dumps(message, default=str))
            if message.get("type") == "session.revoked":
                await conn.ws.close(code=4401)
                return


hub = Hub()


# ---------------------------------------------------------------------- eventos tras commit
def area_audience(db: Session, area_id: uuid.UUID | None) -> set[uuid.UUID] | None:
    """Miembros del área + administradores (que tienen acceso implícito). None = todos."""
    from wadima.models import AreaMember, User

    if area_id is None:
        return None
    members = set(db.execute(select(AreaMember.user_id).where(AreaMember.area_id == area_id)).scalars())
    admins = set(db.execute(select(User.id).where(User.role == "admin", User.is_active)).scalars())
    return members | admins


def emit(
    db: Session,
    type_: str,
    *,
    area_id: uuid.UUID | None = None,
    audience: set[uuid.UUID] | None = None,
    everyone: bool = False,
    **data: Any,
) -> None:
    """Programa un evento para publicarse cuando la transacción de `db` haga commit."""
    if not everyone and audience is None:
        audience = area_audience(db, area_id)
    message = {"type": type_, "area_id": str(area_id) if area_id else None, **data}
    db.info.setdefault("pending_events", []).append((message, audience))


@event.listens_for(Session, "after_commit")
def _flush_events(session: Session) -> None:
    pending = session.info.pop("pending_events", None)
    if pending:
        for message, audience in pending:
            hub.publish(message, audience)


@event.listens_for(Session, "after_rollback")
def _drop_events(session: Session) -> None:
    session.info.pop("pending_events", None)
