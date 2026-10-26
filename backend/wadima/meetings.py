"""Sala de reuniones: señalización WebRTC sobre el WebSocket de WADIMA (topología en malla para pocos socios).

El servidor nunca recibe audio: solo reenvía ofertas, respuestas y candidatos ICE entre participantes de la
misma sala y anuncia quién entra, sale o silencia su micrófono. El remitente lo fija el servidor (no se confía
en el cliente). Nada se graba ni se almacena; el estado vive en memoria del proceso y se pierde al reiniciar.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from wadima.config import get_settings
from wadima.realtime import Connection, hub

MAX_PARTICIPANTS = 8  # malla: cada socio envía su audio a todos los demás
MAX_SIGNAL_BYTES = 64 * 1024
SIGNAL_WINDOW_SECONDS = 10
SIGNAL_WINDOW_MAX = 400


@dataclass(eq=False)
class Participant:
    peer_id: str
    user_id: uuid.UUID
    display_name: str
    room_id: uuid.UUID
    conn: Connection
    muted: bool = True
    prev_declared: str = "available"
    joined_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    window_start: float = field(default_factory=time.monotonic)
    window_count: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "user_id": str(self.user_id),
            "display_name": self.display_name,
            "muted": self.muted,
            "joined_at": self.joined_at.isoformat(),
        }


class MeetingError(Exception):
    pass


class MeetingHub:
    def __init__(self) -> None:
        self._rooms: dict[uuid.UUID, dict[str, Participant]] = {}
        self._by_conn: dict[Connection, Participant] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ consultas
    def participants(self, room_id: uuid.UUID) -> list[dict[str, Any]]:
        with self._lock:
            return [p.public() for p in self._rooms.get(room_id, {}).values()]

    def of(self, conn: Connection) -> Participant | None:
        with self._lock:
            return self._by_conn.get(conn)

    def user_rooms(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        with self._lock:
            return {p.room_id for p in self._by_conn.values() if p.user_id == user_id}

    # ------------------------------------------------------------------ cambios
    def join(
        self, conn: Connection, room_id: uuid.UUID, display_name: str, prev_declared: str = "available"
    ) -> tuple[Participant, list[Participant]]:
        """Une la conexión a la sala. Si el mismo socio estaba en la sala desde otra ventana, esa sale."""
        replaced: list[Participant] = []
        with self._lock:
            room = self._rooms.setdefault(room_id, {})
            for p in list(room.values()):
                if p.user_id == conn.user_id and p.conn is not conn:
                    replaced.append(p)
            current = self._by_conn.get(conn)
        if current is not None:
            self.leave(conn)
        for p in replaced:
            self.leave(p.conn)
            hub._put(p.conn, {"type": "meeting.replaced", "room_id": str(room_id)})
        with self._lock:
            room = self._rooms.setdefault(room_id, {})
            if len(room) >= MAX_PARTICIPANTS:
                raise MeetingError(f"La sala admite como máximo {MAX_PARTICIPANTS} participantes")
            me = Participant(
                peer_id=secrets.token_urlsafe(9),
                user_id=conn.user_id,
                display_name=display_name,
                room_id=room_id,
                conn=conn,
                prev_declared=prev_declared,
            )
            others = list(room.values())
            room[me.peer_id] = me
            self._by_conn[conn] = me
        for p in others:
            hub._put(p.conn, {"type": "meeting.peer_joined", "room_id": str(room_id), "peer": me.public()})
        return me, others

    def leave(self, conn: Connection) -> Participant | None:
        with self._lock:
            me = self._by_conn.pop(conn, None)
            if me is None:
                return None
            room = self._rooms.get(me.room_id, {})
            room.pop(me.peer_id, None)
            others = list(room.values())
            if not room:
                self._rooms.pop(me.room_id, None)
        for p in others:
            hub._put(p.conn, {"type": "meeting.peer_left", "room_id": str(me.room_id), "peer_id": me.peer_id})
        return me

    def signal(self, conn: Connection, to: str, data: Any) -> bool:
        """Reenvía una descripción SDP o un candidato ICE a otro participante de la misma sala."""
        if not isinstance(data, dict) or not ({"description", "candidate"} & set(data)):
            raise MeetingError("Señal no válida")
        if len(json.dumps(data)) > MAX_SIGNAL_BYTES:
            raise MeetingError("Señal demasiado grande")
        with self._lock:
            me = self._by_conn.get(conn)
            if me is None:
                raise MeetingError("No está en ninguna reunión")
            now = time.monotonic()
            if now - me.window_start > SIGNAL_WINDOW_SECONDS:
                me.window_start, me.window_count = now, 0
            me.window_count += 1
            if me.window_count > SIGNAL_WINDOW_MAX:
                raise MeetingError("Demasiadas señales; espere unos segundos")
            target = self._rooms.get(me.room_id, {}).get(str(to))
        if target is None:
            return False
        hub._put(target.conn, {"type": "meeting.signal", "from": me.peer_id, "data": data})
        return True

    def set_muted(self, conn: Connection, muted: bool) -> Participant | None:
        with self._lock:
            me = self._by_conn.get(conn)
            if me is None:
                return None
            me.muted = bool(muted)
            others = [p for p in self._rooms.get(me.room_id, {}).values() if p is not me]
        for p in others:
            hub._put(
                p.conn,
                {"type": "meeting.peer_state", "room_id": str(me.room_id), "peer_id": me.peer_id, "muted": me.muted},
            )
        return me


meetings = MeetingHub()


def ice_servers(user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Servidores STUN/TURN configurados. Con un TURN propio (coturn, `use-auth-secret`) se generan
    credenciales temporales por usuario según la «TURN REST API»; el secreto nunca sale del servidor."""
    s = get_settings()
    servers: list[dict[str, Any]] = [dict(x) for x in s.ice_servers]
    if s.turn_urls and s.turn_secret:
        expires = int(time.time()) + s.turn_ttl_seconds
        username = f"{expires}:{user_id}"
        digest = hmac.new(s.turn_secret.encode(), username.encode(), hashlib.sha1).digest()
        servers.append(
            {"urls": list(s.turn_urls), "username": username, "credential": base64.b64encode(digest).decode()}
        )
    return servers
