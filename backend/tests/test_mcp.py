"""Servidor MCP probado por HTTP real con el cliente oficial del SDK de MCP (el mismo protocolo que Claude Code)."""

from __future__ import annotations

import io
import socket
import threading
import time

import anyio
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from tests.conftest import UserClient, make_user
from wadima.services import jobs


@pytest.fixture
def live(app):
    """Backend real en un puerto libre (con el gestor de sesiones MCP iniciado por el lifespan)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


def _mcp(url: str, token: str, steps):
    async def main():
        async with (
            create_mcp_http_client(headers={"Authorization": f"Bearer {token}", "User-Agent": "pytest-mcp"}) as http,
            Client(streamable_http_client(f"{url}/mcp", http_client=http)) as client,
        ):
            return await steps(client)

    return anyio.run(main)


def _data(result):
    assert not result.is_error, result.content
    return result.structured_content.get("result", result.structured_content)


def test_requires_valid_token_and_allowed_origin(live, partners):
    _, c2, _ = partners["clients"]
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with httpx2.Client() as http:
        r = http.post(f"{live}/mcp", json=body)
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
        assert http.post(f"{live}/mcp", json=body, headers={"Authorization": "Bearer wmcp_falso"}).status_code == 401
        token = c2.post("/api/ai/mcp-tokens", json={"name": "Claude Code"}).json()["token"]
        evil = {"Authorization": f"Bearer {token}", "Origin": "https://evil.example"}
        assert http.post(f"{live}/mcp", json=body, headers=evil).status_code == 403
        rebinding = {"Authorization": f"Bearer {token}", "Host": "evil.example"}
        assert http.post(f"{live}/mcp", json=body, headers=rebinding).status_code in (400, 403, 421)


def test_status_is_connected_only_after_a_real_call(live, partners):
    _, c2, _ = partners["clients"]
    assert c2.get("/api/ai/options").json()["subscription_mcp"]["status"] == "not_configured"
    created = c2.post("/api/ai/mcp-tokens", json={"name": "Claude Code"})
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith("wmcp_") and created.json()["url"].endswith("/mcp")
    # El token completo no vuelve a mostrarse.
    opts = c2.get("/api/ai/options").json()["subscription_mcp"]
    assert opts["status"] == "tokens_only" and token not in str(opts) and opts["tokens"][0]["hint"] == "…" + token[-4:]

    async def steps(client):
        tools = {t.name for t in (await client.list_tools()).tools}
        me = _data(await client.call_tool("whoami", {}))
        return tools, me

    tools, me = _mcp(live, token, steps)
    assert {"whoami", "search_memory", "list_projects", "list_tasks", "read_document"} <= tools
    assert {"list_pending_tasks", "get_task", "submit_result"} <= tools
    assert me["usuario"] == "socio2" and me["organizacion"] == "WADIMA INDUSTRIE"
    opts = c2.get("/api/ai/options").json()["subscription_mcp"]
    assert opts["status"] == "connected" and opts["tokens"][0]["calls"] >= 2
    # Cliente declarado por el SDK oficial (Claude Code declara «claude-code»).
    assert opts["client"]["name"] == "mcp" and opts["client"]["user_agent"] == "pytest-mcp"
    # Revocado: deja de funcionar al instante.
    c2.delete(f"/api/ai/mcp-tokens/{opts['tokens'][0]['id']}")
    with pytest.raises(Exception):  # noqa: B017 - el cliente del SDK eleva un error de transporte por el 401
        _mcp(live, token, steps)


def test_tools_respect_area_permissions(live, partners, app):
    c1, _, _ = partners["clients"]
    make_user("ajeno")
    outsider = UserClient(app, "ajeno")
    c1.post(
        "/api/library/upload",
        data={"area_id": partners["general"]},
        files={"file": ("plan.md", io.BytesIO(b"# Plan\n\nEl presupuesto anual es de 9000 EUR.\n"))},
    )
    jobs.drain(["document.process"])
    project = c1.post("/api/projects", json={"area_id": partners["general"], "name": "Lanzamiento"}).json()
    c1.post(f"/api/projects/{project['id']}/tasks", json={"title": "Preparar demo"})
    t_admin = c1.post("/api/ai/mcp-tokens", json={}).json()["token"]
    t_out = outsider.post("/api/ai/mcp-tokens", json={}).json()["token"]

    async def steps(client):
        found = _data(await client.call_tool("search_memory", {"query": "presupuesto anual"}))
        projects = _data(await client.call_tool("list_projects", {}))
        tasks = await client.call_tool("list_tasks", {"project_id": project["id"]})
        return found, projects, tasks

    found, projects, tasks = _mcp(live, t_admin, steps)
    assert "9000" in found["contexto"] and "NO CONFIABLES" in found["aviso"]
    assert [p["nombre"] for p in projects] == ["Lanzamiento"]
    assert _data(tasks)[0]["titulo"] == "Preparar demo"
    found, projects, tasks = _mcp(live, t_out, steps)
    assert found["contexto"] == "" and projects == []
    assert tasks.is_error  # proyecto de un área ajena


def test_task_queue_round_trip(live, partners):
    _, c2, c3 = partners["clients"]
    token = c2.post("/api/ai/mcp-tokens", json={}).json()["token"]
    task = c2.post("/api/ai/tasks", json={"prompt": "Resume el estado del proyecto", "use_memory": False})
    assert task.status_code == 201 and task.json()["status"] == "queued"
    other = c3.post("/api/ai/tasks", json={"prompt": "Tarea de otro socio"}).json()
    tid = task.json()["id"]

    async def steps(client):
        pending = _data(await client.call_tool("list_pending_tasks", {}))
        detail = _data(await client.call_tool("get_task", {"task_id": tid}))
        foreign = await client.call_tool("get_task", {"task_id": other["id"]})
        done = _data(await client.call_tool("submit_result", {"task_id": tid, "output": "## Estado\n\nTodo en orden."}))
        twice = await client.call_tool("submit_result", {"task_id": tid, "output": "otra vez"})
        return pending, detail, foreign, done, twice

    pending, detail, foreign, done, twice = _mcp(live, token, steps)
    assert [p["task_id"] for p in pending] == [tid]  # solo las tareas propias
    assert detail["encargo"] == "Resume el estado del proyecto"
    assert foreign.is_error and done["estado"] == "done" and twice.is_error
    run = c2.get(f"/api/ai/runs/{tid}").json()
    assert run["status"] == "done" and run["output"].startswith("## Estado") and run["provider"] == "claude_mcp"
    assert c3.get(f"/api/ai/runs/{tid}").status_code == 404  # privada hasta que se comparta
    cancel = c3.post(f"/api/ai/tasks/{other['id']}/cancel")
    assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"


def test_project_task_assigned_to_a_partners_claude(live, partners):
    c1, c2, _ = partners["clients"]
    project = c1.post("/api/projects", json={"area_id": partners["general"], "name": "Lanzamiento"}).json()
    # Sin Claude conectado no se le puede asignar trabajo.
    r = c1.post(f"/api/projects/{project['id']}/tasks", json={"title": "Redactar el anuncio", "ai_assignee_id": c2.id})
    assert r.status_code == 422 and "no ha conectado su Claude" in r.json()["detail"]
    token = c2.post("/api/ai/mcp-tokens", json={}).json()["token"]

    async def whoami(client):
        return _data(await client.call_tool("whoami", {}))

    _mcp(live, token, whoami)  # primera conexión real de su Claude Code
    agents = {a["user_id"]: a for a in c1.get("/api/ai/agents").json()}
    assert agents[c2.id]["assignable"] and agents[c2.id]["subscription"] and not agents[c1.id]["assignable"]
    task = c1.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "Redactar el anuncio", "description": "Tono cercano, 3 párrafos.", "ai_assignee_id": c2.id},
    ).json()
    assert task["ai_assignee_id"] == c2.id and task["ai_run"]["status"] == "queued"
    assert c2.get("/api/notifications").json()["items"][0]["kind"] == "ai.task"
    people = {p["id"]: p for p in c1.get("/api/office/state").json()["people"]}
    assert people[c2.id]["claude"]["state"] == "waiting" and people[c2.id]["claude"]["pending"] == 1

    async def work(client):
        pending = _data(await client.call_tool("list_pending_tasks", {}))
        tid = pending[0]["task_id"]
        detail = _data(await client.call_tool("get_task", {"task_id": tid}))
        mid = {p["id"]: p for p in c1.get("/api/office/state").json()["people"]}[c2.id]["claude"]
        done = _data(
            await client.call_tool("submit_result", {"task_id": tid, "output": "Anuncio listo: ¡Llega WADIMA!"})
        )
        return pending, detail, mid, done

    pending, detail, mid, done = _mcp(live, token, work)
    assert pending[0]["proyecto"] == "Lanzamiento" and pending[0]["tarea_del_proyecto"] == "Redactar el anuncio"
    assert pending[0]["asignada_por"] == "Socio1" and "Tono cercano" in detail["encargo"]
    assert mid["state"] == "working" and mid["working_on"]["title"] == "Redactar el anuncio"
    assert done["estado"] == "done"
    t = next(x for x in c1.get(f"/api/projects/{project['id']}/tasks").json() if x["id"] == task["id"])
    assert t["status"] == "done" and t["started_at"] and t["ai_run"]["status"] == "done"
    comments = c1.get("/api/comments", params={"target_type": "task", "target_id": task["id"]}).json()
    assert "Claude de Socio2" in comments[0]["body"] and "Llega WADIMA" in comments[0]["body"]
    assert c1.get(f"/api/ai/runs/{t['ai_run']['id']}").status_code == 200  # compartida con el proyecto
    assert any(n["kind"] == "ai.result" for n in c1.get("/api/notifications").json()["items"])
    activity = [a["summary"] for a in c1.get("/api/activity", params={"project_id": project["id"]}).json()]
    assert any("Claude de Socio2 terminó" in s for s in activity)

    # Quitar la IA o reasignar cancela lo que estuviera en cola.
    t2 = c1.post(f"/api/projects/{project['id']}/tasks", json={"title": "Otra", "ai_assignee_id": c2.id}).json()
    r = c1.patch(f"/api/tasks/{t2['id']}", json={"version": t2["version"], "clear_ai_assignee": True})
    assert r.json()["ai_assignee_id"] is None and r.json()["ai_run"]["status"] == "cancelled"
