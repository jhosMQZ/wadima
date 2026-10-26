"""Otros proveedores de IA con la clave propia de cada socio: OpenAI, Google Gemini, DeepSeek y APIs compatibles
con OpenAI. Las respuestas salen de servidores simulados con el formato documentado de cada API."""

from __future__ import annotations

import json
import socket

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import UserClient, make_user
from wadima.ai import http_providers, providers
from wadima.db import session_scope
from wadima.models import AICredential, AIRun

OPENAI1 = "sk-proj-socio1-AAAAAAAAAAAAAAAAAAAAAAAA1111"
OPENAI2 = "sk-proj-socio2-BBBBBBBBBBBBBBBBBBBBBBBB2222"
DEEPSEEK1 = "sk-deepseek-socio1-CCCCCCCCCCCCCCCCCC3333"
COMPAT1 = "or-socio1-DDDDDDDDDDDDDDDDDDDDDDDDDD4444"
GEM1 = "AIzaSy-socio1-EEEEEEEEEEEEEEEEEEEEEEEE5555"
BAD = "sk-proj-revocada-FFFFFFFFFFFFFFFFFFFFFF0000"

OPENAI_KEYS = {OPENAI1: "socio1", OPENAI2: "socio2", DEEPSEEK1: "socio1", COMPAT1: "socio1", "ollama": "local"}
MODELS = {
    "api.openai.com": ["gpt-4.1", "gpt-5", "text-embedding-3-small", "whisper-1", "dall-e-3"],
    "api.deepseek.com": ["deepseek-chat", "deepseek-reasoner"],
    "api.compatible.example": ["meta-llama/llama-3.3-70b-instruct", "mistralai/mistral-small"],
    "127.0.0.1": ["llama3.2"],
}


class FakeAPIs:
    """Subconjunto de las APIs de OpenAI (y compatibles) y de Gemini."""

    def __init__(self):
        self.calls: list[dict] = []
        self.mode: dict[str, str] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.calls.append(
            {
                "host": request.url.host,
                "path": request.url.path,
                "auth": request.headers.get("authorization"),
                "goog": request.headers.get("x-goog-api-key"),
                "body": body,
            }
        )
        if request.url.host == "generativelanguage.googleapis.com":
            return self._gemini(request, body)
        return self._openai_like(request, body)

    def _openai_like(self, request: httpx.Request, body: dict) -> httpx.Response:
        host = request.url.host
        auth = request.headers.get("authorization", "")
        key = auth[7:] if auth.startswith("Bearer ") else None
        if key not in OPENAI_KEYS:
            return httpx.Response(
                401,
                json={"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}},
            )
        mode = self.mode.get(host)
        if mode == "nobalance":
            return httpx.Response(402, json={"error": {"message": "Insufficient Balance", "type": "unknown_error"}})
        if mode == "ratelimit":
            return httpx.Response(
                429, headers={"retry-after": "7"}, json={"error": {"message": "Rate limit reached", "type": "requests"}}
            )
        headers = {"x-request-id": f"req_{host}", "x-ratelimit-remaining-requests": "99"}
        if request.method == "GET" and request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                headers=headers,
                json={"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS[host]]},
            )
        if request.method == "POST" and request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": f"Respuesta de {host} para {OPENAI_KEYS[key]} [1]",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
                },
            )
        return httpx.Response(404, json={"error": {"message": "Not found"}})

    def _gemini(self, request: httpx.Request, body: dict) -> httpx.Response:
        if request.headers.get("x-goog-api-key") != GEM1:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": 400,
                        "message": "API key not valid. Please pass a valid API key.",
                        "status": "INVALID_ARGUMENT",
                        "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}],
                    }
                },
            )
        path = request.url.path
        if request.method == "GET" and path == "/v1beta/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-pro",
                            "displayName": "Gemini 2.5 Pro",
                            "supportedGenerationMethods": ["generateContent", "countTokens"],
                        },
                        {
                            "name": "models/gemini-2.5-flash",
                            "displayName": "Gemini 2.5 Flash",
                            "supportedGenerationMethods": ["generateContent", "countTokens"],
                        },
                        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
                        {"name": "models/gemma-3-27b-it", "supportedGenerationMethods": ["generateContent"]},
                    ]
                },
            )
        if path.endswith(":countTokens"):
            text = body["contents"][0]["parts"][0]["text"]
            return httpx.Response(200, json={"totalTokens": max(1, len(text) // 4)})
        if path.endswith(":generateContent"):
            if self.mode.get("gemini") == "safety":
                return httpx.Response(
                    200,
                    json={"candidates": [{"finishReason": "SAFETY"}], "usageMetadata": {"promptTokenCount": 10}},
                )
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {"text": "razonamiento interno", "thought": True},
                                    {"text": "Respuesta de Gemini [1]"},
                                ],
                            },
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 40, "thoughtsTokenCount": 12},
                    "modelVersion": path.split("/")[-1].split(":")[0],
                    "responseId": "resp_g1",
                },
            )
        return httpx.Response(404, json={"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})


@pytest.fixture
def apis(monkeypatch):
    fa = FakeAPIs()
    monkeypatch.setattr(http_providers, "TRANSPORT_OVERRIDE", httpx.MockTransport(fa.handler))
    real = socket.getaddrinfo

    def fake_getaddrinfo(host, *args, **kwargs):
        if host == "api.compatible.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        if host == "intranet.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 443))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(providers.socket, "getaddrinfo", fake_getaddrinfo)
    return fa


def connect(c, provider, key, **extra):
    return c.put("/api/ai/credentials", json={"api_key": key, "provider": provider, **extra})


def test_openai_is_verified_secret_hidden_and_run_uses_own_key(partners, apis):
    c1, c2, _ = partners["clients"]
    r = connect(c1, "openai_api", OPENAI1)
    assert r.status_code == 200, r.text
    cred = r.json()
    assert cred["status"] == "verified" and cred["provider_label"] == "OpenAI"
    ids = [m["id"] for m in cred["models"]]
    assert ids == ["gpt-4.1", "gpt-5"]  # sin embeddings, audio ni imagen
    assert cred["default_model"] == "gpt-5"
    assert OPENAI1 not in r.text and cred["key_hint"] == "…1111"
    with session_scope() as db:
        row = db.execute(select(AICredential).where(AICredential.provider == "openai_api")).scalar_one()
        assert OPENAI1.encode() not in row.secret_encrypted
    connect(c2, "openai_api", OPENAI2)

    run = c1.post("/api/ai/runs", json={"prompt": "estado del proyecto", "provider": "openai_api", "use_memory": False})
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["provider"] == "openai_api" and body["provider_label"] == "OpenAI" and body["status"] == "done"
    assert body["output"] == "Respuesta de api.openai.com para socio1 [1]"
    assert body["usage"]["input_tokens"] == 120 and body["usage"]["output_tokens"] == 30
    sent = [c for c in apis.calls if c["path"] == "/v1/chat/completions"][-1]
    assert sent["auth"] == f"Bearer {OPENAI1}"
    assert (
        sent["body"]["messages"][0]["role"] == "system"
        and "DATOS NO CONFIABLES" in sent["body"]["messages"][0]["content"]
    )
    assert "tools" not in sent["body"] and "max_tokens" not in sent["body"]
    # Cada socio usa solo su propia clave
    c2.post("/api/ai/runs", json={"prompt": "hola", "provider": "openai_api", "use_memory": False})
    assert [c for c in apis.calls if c["path"] == "/v1/chat/completions"][-1]["auth"] == f"Bearer {OPENAI2}"
    # Un modelo que la cuenta no tiene no se acepta
    bad = c1.post("/api/ai/runs", json={"prompt": "hola", "provider": "openai_api", "model": "gpt-inventado"})
    assert bad.status_code == 422


def test_invalid_keys_are_never_shown_as_connected(partners, apis):
    c1 = partners["clients"][0]
    r = connect(c1, "openai_api", BAD).json()
    assert r["status"] == "error" and "rechazó" in r["last_error"]
    assert c1.post("/api/ai/runs", json={"prompt": "hola", "provider": "openai_api"}).status_code == 400
    g = connect(c1, "gemini_api", "AIzaSy-clave-que-no-existe-XXXXXXXXXXXX").json()
    assert g["status"] == "error" and "Gemini" in g["last_error"]
    # Formatos de otros servicios se rechazan antes de llamar a nadie
    assert connect(c1, "openai_api", "sk-ant-api03-esto-es-de-anthropic-XXXXXXXX").status_code == 422
    assert connect(c1, "openai_api", "sk-admin-XXXXXXXXXXXXXXXXXXXXXXXXXXXX").status_code == 422
    assert connect(c1, "anthropic_api", OPENAI1).status_code == 422
    assert connect(c1, "deepseek_api", "clave-sin-prefijo-XXXXXXXXXXXXXXXX").status_code == 422
    assert connect(c1, "inventado_api", OPENAI1).status_code == 422


def test_gemini_run_excludes_thoughts_counts_tokens_and_reports_refusals(partners, apis):
    c1 = partners["clients"][0]
    cred = connect(c1, "gemini_api", GEM1).json()
    assert cred["status"] == "verified"
    assert [m["id"] for m in cred["models"]] == ["gemini-2.5-flash", "gemini-2.5-pro"]
    assert cred["default_model"] == "gemini-2.5-pro"
    run = c1.post("/api/ai/runs", json={"prompt": "resumen", "provider": "gemini_api", "use_memory": False}).json()
    assert run["status"] == "done" and run["output"] == "Respuesta de Gemini [1]"  # sin el razonamiento interno
    assert run["usage"]["input_tokens"] == 200 and run["usage"]["output_tokens"] == 40
    sent = [c for c in apis.calls if c["path"].endswith(":generateContent")][-1]
    assert sent["path"] == "/v1beta/models/gemini-2.5-pro:generateContent" and sent["goog"] == GEM1
    assert "DATOS NO CONFIABLES" in sent["body"]["systemInstruction"]["parts"][0]["text"]
    count = c1.post("/api/ai/count-tokens", json={"text": "x" * 400, "provider": "gemini_api"})
    assert count.status_code == 200 and count.json()["input_tokens"] == 100
    apis.mode["gemini"] = "safety"
    refused = c1.post("/api/ai/runs", json={"prompt": "algo", "provider": "gemini_api", "use_memory": False}).json()
    assert refused["status"] == "refused" and "safety" in refused["error"]


def test_deepseek_uses_its_own_endpoint_and_reports_balance(partners, apis):
    c1 = partners["clients"][0]
    cred = connect(c1, "deepseek_api", DEEPSEEK1).json()
    assert cred["status"] == "verified" and cred["default_model"] == "deepseek-chat"
    run = c1.post("/api/ai/runs", json={"prompt": "hola", "provider": "deepseek_api", "use_memory": False}).json()
    assert run["output"] == "Respuesta de api.deepseek.com para socio1 [1]"
    assert [c for c in apis.calls if c["host"] == "api.deepseek.com"][-1]["path"] == "/chat/completions"
    apis.mode["api.deepseek.com"] = "nobalance"
    r = c1.post("/api/ai/runs", json={"prompt": "hola", "provider": "deepseek_api", "use_memory": False})
    assert r.status_code == 400 and "Saldo insuficiente" in r.json()["detail"]["message"]
    apis.mode["api.deepseek.com"] = "ratelimit"
    r = c1.post("/api/ai/runs", json={"prompt": "hola", "provider": "deepseek_api", "use_memory": False})
    assert r.status_code == 429 and r.json()["detail"]["retry_after"] == 7
    with session_scope() as db:
        statuses = [x.status for x in db.execute(select(AIRun).where(AIRun.provider == "deepseek_api")).scalars()]
    assert statuses.count("error") == 2


def test_compatible_api_requires_safe_public_address(app, partners, apis):
    c1, c2, _ = partners["clients"]  # socio1 es administrador
    ok = connect(c2, "openai_compatible", COMPAT1, base_url="https://api.compatible.example/v1/").json()
    assert ok["status"] == "verified" and ok["base_url"] == "https://api.compatible.example/v1"
    assert ok["default_model"] == "meta-llama/llama-3.3-70b-instruct"
    run = c2.post("/api/ai/runs", json={"prompt": "hola", "provider": "openai_compatible", "use_memory": False}).json()
    assert run["output"].startswith("Respuesta de api.compatible.example")
    for url, code in (
        ("http://api.compatible.example/v1", 422),  # sin cifrar fuera del propio equipo
        ("https://192.168.1.10/v1", 422),  # red interna (IP)
        ("https://intranet.example/v1", 422),  # red interna (resuelve a una IP privada)
        ("https://169.254.169.254/latest", 422),  # metadatos de la nube
        ("https://user:pass@api.compatible.example/v1", 422),
        ("https://api.compatible.example/v1?x=1", 422),
        ("ftp://api.compatible.example", 422),
        ("http://127.0.0.1:11434/v1", 403),  # servidor local: solo administradores
    ):
        assert connect(c2, "openai_compatible", COMPAT1, base_url=url).status_code == code, url
    local = connect(c1, "openai_compatible", "ollama", base_url="http://127.0.0.1:11434/v1").json()
    assert local["status"] == "verified" and local["default_model"] == "llama3.2"


def test_default_provider_catalog_options_and_disconnect(partners, apis):
    c1, c2, _ = partners["clients"]
    connect(c2, "gemini_api", GEM1)
    # Sin indicar proveedor se usa el único conectado
    run = c2.post("/api/ai/runs", json={"prompt": "hola", "use_memory": False}).json()
    assert run["provider"] == "gemini_api"
    creds = c1.get("/api/ai/credentials").json()
    assert [p["key"] for p in creds["catalog"]] == [
        "anthropic_api",
        "openai_api",
        "gemini_api",
        "deepseek_api",
        "openai_compatible",
    ]
    partner = next(p for p in creds["partners"] if p["user_id"] == c2.id)
    assert partner["apis"] == ["Gemini"] and GEM1 not in json.dumps(creds)
    opts = c2.get("/api/ai/options").json()
    gem = next(o for o in opts["other_apis"] if o["provider"] == "gemini_api")
    assert gem["status"] == "verified" and opts["api_key"]["status"] == "not_configured"
    # OpenAI no ofrece recuento de tokens sin generar: se dice en lugar de inventarlo
    connect(c1, "openai_api", OPENAI1)
    r = c1.post("/api/ai/count-tokens", json={"text": "hola", "provider": "openai_api"})
    assert r.status_code == 400 and "no ofrece" in r.json()["detail"]
    d = c2.delete("/api/ai/credentials?provider=gemini_api").json()
    assert d["ok"] and "aistudio.google.com" in d["note"]
    assert c2.post("/api/ai/runs", json={"prompt": "hola", "use_memory": False}).status_code == 400


def test_member_without_admin_cannot_point_to_this_machine(app, partners, apis):
    make_user("invitado")
    guest = UserClient(app, "invitado")
    r = connect(guest, "openai_compatible", "ollama", base_url="http://localhost:11434/v1")
    assert r.status_code == 403


def test_default_model_is_always_taken_from_the_account_list():
    assert providers.pick_default("openai_api", ["gpt-4o", "gpt-4.1"]) == "gpt-4.1"
    assert providers.pick_default("openai_api", ["o4-mini", "gpt-4o-mini"]) in ("o4-mini", "gpt-4o-mini")
    assert providers.pick_default("gemini_api", ["gemini-2.0-flash", "gemini-3-pro-preview"]) == "gemini-2.0-flash"
    assert providers.pick_default("deepseek_api", ["deepseek-reasoner"]) == "deepseek-reasoner"
    assert providers.pick_default("openai_compatible", []) == ""
