from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from wadima.realtime import hub


def _recv_until(ws, type_: str, limit: int = 20, **match):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == type_ and all(msg.get(k) == v for k, v in match.items()):
            return msg
    raise AssertionError(f"No llegó un evento {type_}")


def test_ws_requires_session(server):
    with pytest.raises(WebSocketDisconnect), server.websocket_connect("/ws") as ws:
        ws.receive_json()


def test_ws_rejects_foreign_origin(server, partners):
    c1 = partners["clients"][0]
    server.cookies.set("wadima_session", c1.http.cookies.get("wadima_session"))
    with (
        pytest.raises(WebSocketDisconnect),
        server.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws,
    ):
        ws.receive_json()


def test_presence_and_events_reach_partners(server, partners):
    c1, c2, c3 = partners["clients"]
    server.cookies.set("wadima_session", c1.http.cookies.get("wadima_session"))
    with server.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        assert hub.is_online(__import__("uuid").UUID(c1.id))
        # Estado declarado por otro socio -> evento de presencia
        c2.put("/api/me/status", json={"declared": "meeting", "message": "Revisión semanal"})
        msg = _recv_until(ws, "presence.changed", user_id=c2.id)
        assert msg["area_id"] is None
        # La oficina refleja el estado real
        state = c1.get("/api/office/state").json()
        p1 = next(p for p in state["people"] if p["id"] == c1.id)
        p2 = next(p for p in state["people"] if p["id"] == c2.id)
        assert p1["online"] is True and p1["state"] == "online"
        assert p2["online"] is False and p2["state"] == "offline"  # c2 no tiene socket abierto
        # Eventos de área llegan a los miembros
        c3.post(f"/api/areas/{partners['general']}/enter")
        assert _recv_until(ws, "presence.changed", user_id=c3.id)
        ws.send_json({"type": "ping"})
        _recv_until(ws, "pong")


def test_area_events_not_leaked_to_non_members(server, partners, app):
    from tests.conftest import UserClient, make_user

    c1, c2, _ = partners["clients"]
    make_user("ajeno")
    ajeno = UserClient(app, "ajeno")
    server.cookies.set("wadima_session", ajeno.http.cookies.get("wadima_session"))
    with server.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        area = c2.post("/api/areas", json={"name": "Privada"}).json()
        c2.patch(f"/api/areas/{area['id']}", json={"version": 1, "description": "secreto"})
        ws.send_json({"type": "ping"})
        # Lo siguiente que recibe es el pong: ningún evento del área privada.
        msg = ws.receive_json()
        while msg["type"] == "presence.changed":
            msg = ws.receive_json()
        assert msg["type"] == "pong"
