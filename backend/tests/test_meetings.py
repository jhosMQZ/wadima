from __future__ import annotations

import base64
import hashlib
import hmac
import uuid

import pytest

from tests.conftest import UserClient, make_user
from wadima.config import get_settings
from wadima.db import session_scope
from wadima.models import UserStatus


def _cookie(client) -> dict:
    return {"cookie": f"wadima_session={client.http.cookies.get('wadima_session')}"}


def _recv(ws, type_: str, limit: int = 30, **match):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == type_ and all(msg.get(k) == v for k, v in match.items()):
            return msg
    raise AssertionError(f"No llegó {type_}")


@pytest.fixture
def meeting_room(partners):
    c1 = partners["clients"][0]
    templates = c1.post("/api/org/templates/meeting/enable").json()
    return next(t for t in templates if t["key"] == "meeting")["area_id"]


def _join(ws, area_id: str) -> dict:
    assert ws.receive_json()["type"] == "hello"
    ws.send_json({"type": "meeting.join", "area_id": area_id})
    return _recv(ws, "meeting.joined")


def test_signaling_between_partners(server, partners, meeting_room):
    c1, c2, _ = partners["clients"]
    with (
        server.websocket_connect("/ws", headers=_cookie(c1)) as a,
        server.websocket_connect("/ws", headers=_cookie(c2)) as b,
    ):
        ja = _join(a, meeting_room)
        assert ja["participants"] == [] and ja["ice_servers"] == []
        jb = _join(b, meeting_room)
        assert [p["user_id"] for p in jb["participants"]] == [c1.id]
        joined = _recv(a, "meeting.peer_joined")
        assert joined["peer"]["peer_id"] == jb["peer_id"] and joined["peer"]["muted"] is True
        # El recién llegado envía la oferta; el servidor fija el remitente (no se confía en el cliente).
        offer = {"description": {"type": "offer", "sdp": "v=0..."}}
        b.send_json({"type": "meeting.signal", "to": ja["peer_id"], "from": "suplantado", "data": offer})
        got = _recv(a, "meeting.signal")
        assert got["from"] == jb["peer_id"] and got["data"] == offer
        a.send_json({"type": "meeting.signal", "to": jb["peer_id"], "data": {"candidate": {"candidate": "c"}}})
        assert _recv(b, "meeting.signal")["data"]["candidate"] == {"candidate": "c"}
        # Estado del micrófono
        b.send_json({"type": "meeting.state", "muted": False})
        state = _recv(a, "meeting.peer_state")
        assert state["peer_id"] == jb["peer_id"] and state["muted"] is False
        # Señales inválidas o demasiado grandes se rechazan con un error comprensible.
        a.send_json({"type": "meeting.signal", "to": jb["peer_id"], "data": {"otro": 1}})
        assert _recv(a, "meeting.error")["message"] == "Señal no válida"
        a.send_json({"type": "meeting.signal", "to": jb["peer_id"], "data": {"candidate": "x" * 70000}})
        assert _recv(a, "meeting.error")["message"] == "Señal demasiado grande"
        a.send_json({"type": "meeting.signal", "to": "nadie", "data": offer})
        assert _recv(a, "meeting.error")["code"] == "peer_gone"
        rooms = c1.get("/api/meetings/rooms").json()
        assert {p["user_id"] for p in rooms[0]["participants"]} == {c1.id, c2.id}
        # Presencia: los dos aparecen en la sala de reuniones
        people = {p["id"]: p for p in c1.get("/api/office/state").json()["people"]}
        assert people[c2.id]["declared"] == "meeting" and people[c2.id]["room_key"] == "meeting"
        b.send_json({"type": "meeting.leave"})
        assert _recv(b, "meeting.left")
        assert _recv(a, "meeting.peer_left")["peer_id"] == jb["peer_id"]
        with session_scope() as db:
            assert db.get(UserStatus, uuid.UUID(c2.id)).declared == "available"
        assert [p["user_id"] for p in c1.get("/api/meetings/rooms").json()[0]["participants"]] == [c1.id]


def test_disconnect_removes_participant(server, partners, meeting_room):
    c1, c2, _ = partners["clients"]
    with server.websocket_connect("/ws", headers=_cookie(c1)) as a:
        _join(a, meeting_room)
        with server.websocket_connect("/ws", headers=_cookie(c2)) as b:
            jb = _join(b, meeting_room)
            _recv(a, "meeting.peer_joined")
        # Cerrar la ventana (o perder la conexión) saca al socio de la sala.
        assert _recv(a, "meeting.peer_left")["peer_id"] == jb["peer_id"]


def test_same_partner_from_another_window_replaces_the_first(server, partners, meeting_room):
    c2 = partners["clients"][1]
    second = UserClient(server.app, "socio2")
    with (
        server.websocket_connect("/ws", headers=_cookie(c2)) as w1,
        server.websocket_connect("/ws", headers=_cookie(second)) as w2,
    ):
        _join(w1, meeting_room)
        j2 = _join(w2, meeting_room)
        assert j2["participants"] == []
        assert _recv(w1, "meeting.replaced")["room_id"] == meeting_room


def test_access_control(server, partners, meeting_room, app):
    c1, _, _ = partners["clients"]
    make_user("ajeno")
    outsider = UserClient(app, "ajeno")
    with server.websocket_connect("/ws", headers=_cookie(outsider)) as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "meeting.join", "area_id": meeting_room})
        assert "No tiene acceso" in _recv(ws, "meeting.error")["message"]
        ws.send_json({"type": "meeting.join", "area_id": "no-es-uuid"})
        assert _recv(ws, "meeting.error")["message"] == "Sala no válida"
        ws.send_json({"type": "meeting.signal", "to": "x", "data": {"candidate": "c"}})
        assert _recv(ws, "meeting.error")["message"] == "No está en ninguna reunión"
    # Un área que no es sala de reuniones o una sala deshabilitada no admiten audio.
    with server.websocket_connect("/ws", headers=_cookie(c1)) as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "meeting.join", "area_id": partners["general"]})
        assert _recv(ws, "meeting.error")["code"] == "rejected"
        c1.post("/api/org/templates/meeting/disable")
        ws.send_json({"type": "meeting.join", "area_id": meeting_room})
        assert _recv(ws, "meeting.error")["code"] == "rejected"


def test_ice_configuration_with_turn_credentials(partners, monkeypatch):
    c1 = partners["clients"][0]
    cfg = c1.get("/api/meetings/config").json()
    assert cfg == {
        "ice_servers": [],
        "turn_configured": False,
        "max_participants": 8,
        "recording": False,
        "topology": "mesh",
    }
    s = get_settings()
    monkeypatch.setattr(s, "turn_urls", ["turn:turn.example.org:3478?transport=udp"])
    monkeypatch.setattr(s, "turn_secret", "secreto-compartido")
    monkeypatch.setattr(s, "ice_servers", [{"urls": "stun:stun.example.org:3478"}])
    cfg = c1.get("/api/meetings/config").json()
    assert cfg["turn_configured"] is True and cfg["ice_servers"][0] == {"urls": "stun:stun.example.org:3478"}
    turn = cfg["ice_servers"][1]
    expires, user = turn["username"].split(":")
    assert user == c1.id and int(expires) > 0
    expected = base64.b64encode(hmac.new(b"secreto-compartido", turn["username"].encode(), hashlib.sha1).digest())
    assert turn["credential"] == expected.decode()
    assert "secreto-compartido" not in str(cfg)
