from __future__ import annotations

import io
import json

import httpx2
import pytest
from sqlalchemy import select

from tests.conftest import UserClient, make_user
from wadima.ai import anthropic_provider
from wadima.db import session_scope
from wadima.models import AICredential, AuditLog
from wadima.services import jobs

KEY1 = "sk-ant-api03-socio1-AAAAAAAAAAAAAAAAAAAAAAAA1111"
KEY2 = "sk-ant-api03-socio2-BBBBBBBBBBBBBBBBBBBBBBBB2222"
BAD = "sk-ant-api03-revocada-CCCCCCCCCCCCCCCCCCCCC0000"


class FakeAnthropic:
    """Subconjunto de la API de Anthropic: /v1/models, /v1/messages y /v1/messages/count_tokens."""

    def __init__(self):
        self.calls: list[dict] = []
        self.mode = "ok"

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        auth = request.headers.get("authorization", "")
        key = request.headers.get("x-api-key") or (auth[7:] if auth.startswith("Bearer ") else None)
        body = json.loads(request.content) if request.content else {}
        self.calls.append(
            {"path": request.url.path, "key": key, "body": body, "beta": request.headers.get("anthropic-beta")}
        )
        if key not in (KEY1, KEY2):
            return httpx2.Response(
                401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
            )
        if self.mode == "ratelimit":
            return httpx2.Response(
                429,
                headers={"retry-after": "30"},
                json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}},
            )
        rl = {
            "anthropic-ratelimit-requests-remaining": "49",
            "anthropic-ratelimit-tokens-remaining": "39000",
            "request-id": "req_test_1",
            "anthropic-organization-id": f"org-{key[14:20]}",
        }
        if request.url.path == "/v1/models":
            return httpx2.Response(
                200,
                headers=rl,
                json={
                    "data": [
                        {
                            "type": "model",
                            "id": "claude-opus-5-5",
                            "display_name": "Claude Opus 5.5",
                            "created_at": "2026-09-01T00:00:00Z",
                        },
                        {
                            "type": "model",
                            "id": "claude-sonnet-5-5",
                            "display_name": "Claude Sonnet 5.5",
                            "created_at": "2026-09-01T00:00:00Z",
                        },
                    ],
                    "has_more": False,
                    "first_id": "claude-opus-5-5",
                    "last_id": "claude-sonnet-5-5",
                },
            )
        if request.url.path == "/v1/messages/count_tokens":
            text = body["messages"][0]["content"]
            return httpx2.Response(200, headers=rl, json={"input_tokens": max(1, len(text) // 3)})
        if request.url.path == "/v1/messages":
            if self.mode == "refusal":
                return httpx2.Response(
                    200,
                    headers=rl,
                    json={
                        "id": "msg_r",
                        "type": "message",
                        "role": "assistant",
                        "model": body["model"],
                        "content": [],
                        "stop_reason": "refusal",
                        "stop_sequence": None,
                        "stop_details": {"type": "refusal", "category": "cyber", "explanation": "no"},
                        "usage": {"input_tokens": 10, "output_tokens": 0},
                    },
                )
            owner = "socio1" if key == KEY1 else "socio2"
            return httpx2.Response(
                200,
                headers=rl,
                json={
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "content": [{"type": "text", "text": f"Respuesta para {owner} [1]"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 321,
                        "output_tokens": 45,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            )
        return httpx2.Response(404, json={"type": "error", "error": {"type": "not_found_error", "message": "?"}})


@pytest.fixture
def fake(monkeypatch):
    fa = FakeAnthropic()
    monkeypatch.setattr(anthropic_provider, "TRANSPORT_OVERRIDE", httpx2.MockTransport(fa.handler))
    return fa


def connect(c, key):
    return c.put("/api/ai/credentials", json={"api_key": key, "label": "personal"})


def test_connection_is_verified_and_secret_never_exposed(partners, fake):
    c1, c2, _ = partners["clients"]
    r = connect(c1, KEY1)
    assert r.status_code == 200, r.text
    mine = r.json()
    assert mine["status"] == "verified" and mine["key_hint"] == "…1111"
    assert mine["default_model"] == "claude-opus-5-5"
    assert {m["id"] for m in mine["models"]} == {"claude-opus-5-5", "claude-sonnet-5-5"}
    assert KEY1 not in json.dumps(mine)
    assert fake.calls[0]["path"] == "/v1/models"  # prueba gratuita, sin generar tokens
    # Cifrada en reposo y ausente de la auditoría
    with session_scope() as db:
        cred = db.execute(select(AICredential)).scalar_one()
        assert KEY1.encode() not in cred.secret_encrypted
        audit_text = json.dumps([a.data for a in db.execute(select(AuditLog)).scalars()])
        assert KEY1 not in audit_text
    # El otro socio solo ve el estado
    view = c2.get("/api/ai/credentials").json()
    assert view["mine"] is None
    assert {p["status"] for p in view["partners"]} == {"verified", "not_configured"}
    assert KEY1 not in json.dumps(view)


def test_invalid_key_is_never_shown_as_connected(partners, fake):
    c1 = partners["clients"][0]
    r = connect(c1, BAD).json()
    assert r["status"] == "error" and "rechazó" in r["last_error"]
    assert c1.post("/api/ai/runs", json={"prompt": "Hola"}).status_code == 400
    assert connect(c1, "clave-sin-formato-valido-123456").status_code == 422
    assert connect(c1, "sk-ant-admin01-xxxxxxxxxxxxxxxxxxxxxxxx").status_code == 422


def test_each_partner_uses_only_their_own_key_and_runs_are_private(partners, fake):
    c1, c2, c3 = partners["clients"]
    connect(c1, KEY1)
    connect(c2, KEY2)
    r1 = c1.post("/api/ai/runs", json={"prompt": "¿Qué hay?", "use_memory": False}).json()
    r2 = c2.post("/api/ai/runs", json={"prompt": "¿Y ahora?", "use_memory": False}).json()
    msg_calls = [c for c in fake.calls if c["path"] == "/v1/messages"]
    assert [c["key"] for c in msg_calls] == [KEY1, KEY2]
    assert r1["output"] == "Respuesta para socio1 [1]" and r2["output"] == "Respuesta para socio2 [1]"
    assert r1["usage"]["input_tokens"] == 321 and r1["usage"]["output_tokens"] == 45
    # Fallback del lado del servidor habilitado para el modelo por defecto
    assert msg_calls[0]["body"]["fallbacks"] == "default" and "server-side-fallback" in msg_calls[0]["beta"]
    # Privacidad: los demás no ven la ejecución hasta que se comparte
    assert c2.get(f"/api/ai/runs/{r1['id']}").status_code == 404
    assert all(x["id"] != r1["id"] for x in c3.get("/api/ai/runs").json())
    # Un socio sin clave no puede usar la de otro
    assert c3.post("/api/ai/runs", json={"prompt": "hola"}).status_code == 400
    assert c1.post(f"/api/ai/runs/{r1['id']}/share", json={"area_id": partners["general"]}).status_code == 200
    assert c2.get(f"/api/ai/runs/{r1['id']}").json()["output"].startswith("Respuesta para socio1")
    acts = c3.get(f"/api/activity?area_id={partners['general']}").json()
    assert any(a["kind"] == "ai.result" for a in acts)
    # c2 no puede compartir la ejecución de c1
    assert c2.post(f"/api/ai/runs/{r1['id']}/share", json={"area_id": partners["general"]}).status_code == 404


def test_run_uses_memory_context_as_untrusted_data_with_permissions(app, partners, fake):
    c1 = partners["clients"][0]
    make_user("ajeno")
    ajeno = UserClient(app, "ajeno")
    connect(c1, KEY1)
    connect(ajeno, KEY2)
    c1.post(
        "/api/library/upload",
        data={"area_id": partners["general"]},
        files={"file": ("plan.md", io.BytesIO(b"# Plan\n\nPresupuesto: 5000 EUR para el proyecto Atlas.\n"))},
    )
    jobs.drain(["document.process"])
    run = c1.post("/api/ai/runs", json={"prompt": "presupuesto del proyecto atlas"}).json()
    assert run["context_refs"] and run["context_refs"][0]["reference"]["document_id"]
    sent = [c for c in fake.calls if c["path"] == "/v1/messages"][-1]["body"]
    assert "DATOS NO CONFIABLES" in sent["system"]
    assert "<contexto>" in sent["messages"][0]["content"] and "5000 EUR" in sent["messages"][0]["content"]
    assert "tools" not in sent  # el modelo no recibe herramientas ni permisos
    # Un usuario sin acceso al área no recibe ese contexto aunque tenga su propia clave
    other = ajeno.post("/api/ai/runs", json={"prompt": "presupuesto del proyecto atlas"}).json()
    assert other["context_refs"] == []
    sent2 = [c for c in fake.calls if c["path"] == "/v1/messages"][-1]["body"]
    assert "5000 EUR" not in sent2["messages"][0]["content"]


def test_errors_refusals_and_rate_limits(partners, fake):
    c1 = partners["clients"][0]
    connect(c1, KEY1)
    fake.mode = "refusal"
    r = c1.post("/api/ai/runs", json={"prompt": "algo", "use_memory": False}).json()
    assert r["status"] == "refused" and "cyber" in r["error"]
    fake.mode = "ratelimit"
    r = c1.post("/api/ai/runs", json={"prompt": "algo", "use_memory": False})
    assert r.status_code == 429 and r.json()["detail"]["kind"] == "rate_limit"
    runs = c1.get("/api/ai/runs").json()
    assert {x["status"] for x in runs} >= {"refused", "error"}


def test_count_tokens_measures_document_versions(partners, fake):
    c1 = partners["clients"][0]
    assert c1.post("/api/ai/count-tokens", json={"text": "hola"}).status_code == 400
    connect(c1, KEY1)
    assert c1.post("/api/ai/count-tokens", json={"text": "hola mundo, esto es una prueba"}).json()["input_tokens"] > 0
    up = c1.post(
        "/api/library/upload",
        data={"area_id": partners["general"]},
        files={"file": ("n.md", io.BytesIO(b"# Titulo\n\nTexto de prueba para medir tokens.\n"))},
    ).json()
    jobs.drain(["document.process"])
    m = c1.post("/api/ai/count-tokens", json={"document_version_id": up["version"]["id"]}).json()
    assert m["method"] == "count_tokens" and m["original"] > 0 and m["markdown"] > 0
    content = c1.get(f"/api/library/documents/{up['document']['id']}/versions/1/content").json()
    assert content["report"]["token_measurement"]["markdown"] == m["markdown"]


def test_disconnect_and_manual_import(partners, fake):
    c1 = partners["clients"][0]
    connect(c1, KEY1)
    r = c1.delete("/api/ai/credentials").json()
    assert "platform.claude.com" in r["note"]
    assert c1.get("/api/ai/credentials").json()["mine"]["status"] == "revoked"
    assert c1.post("/api/ai/runs", json={"prompt": "hola"}).status_code == 400
    imp = c1.post(
        "/api/ai/runs/import", json={"prompt": "Pregunta hecha en claude.ai", "output": "Respuesta copiada"}
    ).json()
    assert imp["provider"] == "manual_import" and imp["usage"]["imported"] is True
