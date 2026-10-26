"""Proveedores de IA por HTTP: OpenAI, DeepSeek, APIs compatibles con OpenAI (OpenRouter, Groq, Mistral,
Ollama…) y Google Gemini.

Mismas reglas que con Anthropic:
- Cada socio usa su propia clave; se pasa siempre de forma explícita y nunca se lee de variables de entorno.
- La prueba de conexión lista los modelos de la cuenta (no genera texto ni consume tokens).
- El contexto recuperado viaja como datos delimitados y el modelo no recibe herramientas.
- No se fija un límite de tokens de salida: cada modelo aplica el suyo (evita rechazos por límites distintos).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from wadima.ai.common import SYSTEM_PROMPT, ProviderError, RunResult, user_content

OPENAI_BASE_URL = "https://api.openai.com/v1"
# DeepSeek publica su API con el formato de OpenAI (GET /models, POST /chat/completions).
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Transporte simulado para pruebas (nunca se usa en producción).
TRANSPORT_OVERRIDE: httpx.BaseTransport | None = None

VERIFY_TIMEOUT = 20.0
RUN_TIMEOUT = 180.0

# Modelos que no sirven para conversar (se ocultan de la lista de OpenAI y compatibles).
_NON_CHAT = re.compile(
    r"embed|whisper|tts|transcri|dall-e|moderation|image|audio|realtime|search|davinci|babbage|computer-use|sora",
    re.I,
)
# Modelos de Gemini que no admiten una conversación con instrucciones de sistema.
_GEMINI_SKIP = re.compile(r"embed|aqa|imagen|veo|tts|image|live|audio|gemma|learnlm|robotics", re.I)


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(transport=TRANSPORT_OVERRIDE, timeout=timeout, follow_redirects=False)


def _retry_after(resp: httpx.Response) -> int | None:
    value = resp.headers.get("retry-after", "")
    return int(value) if value.isdigit() else None


def _error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error")
    except ValueError:
        return resp.text[:300]
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or err)[:300]
    return str(err or resp.text)[:300]


def _network_error(name: str) -> ProviderError:
    return ProviderError(f"No se pudo conectar con {name} (red, proxy o dirección incorrecta).", "network")


# --------------------------------------------------------------------------- OpenAI y compatibles
def _openai_translate(resp: httpx.Response, name: str) -> ProviderError:
    msg = _error_message(resp)
    if resp.status_code == 401:
        return ProviderError(f"{name} rechazó la API key (no válida o revocada).", "auth")
    if resp.status_code == 402:
        return ProviderError(f"Saldo insuficiente en {name}; recargue la cuenta del proveedor ({msg}).", "permission")
    if resp.status_code == 403:
        return ProviderError(f"La API key no tiene permiso para esta operación o modelo ({msg}).", "permission")
    if resp.status_code == 404:
        return ProviderError(f"Modelo o recurso no encontrado en {name} ({msg}).", "not_found")
    if resp.status_code == 429:
        return ProviderError(
            f"Límite de uso o saldo agotado en {name}; inténtelo más tarde ({msg}).", "rate_limit", _retry_after(resp)
        )
    if resp.status_code >= 500:
        return ProviderError(f"Error temporal del servicio de {name} ({resp.status_code}).", "server")
    return ProviderError(f"Solicitud rechazada por {name}: {msg}", "bad_request")


def _openai_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _openai_rate_limits(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower().startswith("x-ratelimit-") or k.lower() == "retry-after"}


def openai_verify(api_key: str, base_url: str = OPENAI_BASE_URL, name: str = "OpenAI") -> dict[str, Any]:
    """GET /models: comprueba la clave sin generar texto."""
    try:
        with _client(VERIFY_TIMEOUT) as c:
            resp = c.get(f"{base_url}/models", headers=_openai_headers(api_key))
    except httpx.HTTPError as exc:
        raise _network_error(name) from exc
    if resp.status_code != 200:
        raise _openai_translate(resp, name)
    try:
        data = resp.json().get("data") or []
    except ValueError as exc:
        raise ProviderError(f"{name} respondió algo que no es una lista de modelos.", "bad_request") from exc
    ids = sorted({str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")})
    chat = [i for i in ids if not _NON_CHAT.search(i)] or ids
    return {
        "models": [{"id": i, "display_name": i} for i in chat[:500]],
        "request_id": resp.headers.get("x-request-id"),
        "organization_id": resp.headers.get("openai-organization"),
        "rate_limits": _openai_rate_limits(resp.headers),
    }


def openai_run(
    api_key: str,
    prompt: str,
    *,
    context: str,
    model: str,
    base_url: str = OPENAI_BASE_URL,
    name: str = "OpenAI",
) -> RunResult:
    """POST /chat/completions con un mensaje de sistema y el mensaje del usuario."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content(prompt, context)},
        ],
    }
    try:
        with _client(RUN_TIMEOUT) as c:
            resp = c.post(f"{base_url}/chat/completions", headers=_openai_headers(api_key), json=payload)
    except httpx.HTTPError as exc:
        raise _network_error(name) from exc
    if resp.status_code != 200:
        raise _openai_translate(resp, name)
    try:
        body = resp.json()
        choice = (body.get("choices") or [{}])[0]
    except (ValueError, AttributeError, IndexError) as exc:
        raise ProviderError(f"{name} devolvió una respuesta con un formato inesperado.", "bad_request") from exc
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # algunos proveedores devuelven partes
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    finish = choice.get("finish_reason")
    refusal = None
    if message.get("refusal") or finish == "content_filter":
        refusal = {"category": finish or "refusal", "explanation": message.get("refusal")}
    usage = body.get("usage") or {}
    return RunResult(
        text=content or "",
        model=str(body.get("model") or model),
        stop_reason=finish,
        usage={
            k: v
            for k, v in {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
            }.items()
            if v is not None
        },
        rate_limits=_openai_rate_limits(resp.headers),
        request_id=resp.headers.get("x-request-id") or body.get("id"),
        refusal=refusal,
    )


# --------------------------------------------------------------------------- Google Gemini
def _gemini_translate(resp: httpx.Response) -> ProviderError:
    msg = _error_message(resp)
    try:
        err = resp.json().get("error") or {}
    except ValueError:
        err = {}
    status = str(err.get("status", "")) if isinstance(err, dict) else ""
    reasons = str(err.get("details", "")) if isinstance(err, dict) else ""
    # Gemini responde 400 INVALID_ARGUMENT (API_KEY_INVALID) cuando la clave no es válida.
    if resp.status_code == 401 or "API_KEY_INVALID" in reasons or "API key not valid" in msg:
        return ProviderError("Google rechazó la API key de Gemini (no válida o revocada).", "auth")
    if resp.status_code == 403 or status == "PERMISSION_DENIED":
        return ProviderError(f"La API key no tiene permiso para usar Gemini ({msg}).", "permission")
    if resp.status_code == 404:
        return ProviderError(f"Modelo de Gemini no encontrado ({msg}).", "not_found")
    if resp.status_code == 429 or status == "RESOURCE_EXHAUSTED":
        return ProviderError(f"Cuota de Gemini agotada; inténtelo más tarde ({msg}).", "rate_limit", _retry_after(resp))
    if resp.status_code >= 500:
        return ProviderError(f"Error temporal del servicio de Gemini ({resp.status_code}).", "server")
    return ProviderError(f"Solicitud rechazada por Gemini: {msg}", "bad_request")


def _gemini_headers(api_key: str) -> dict[str, str]:
    return {"x-goog-api-key": api_key, "Content-Type": "application/json"}


def _gemini_model_path(model: str) -> str:
    model = model.removeprefix("models/")
    return f"models/{quote(model, safe='-._')}"


def gemini_verify(api_key: str) -> dict[str, Any]:
    """GET /models: comprueba la clave y lista los modelos que generan contenido."""
    try:
        with _client(VERIFY_TIMEOUT) as c:
            resp = c.get(f"{GEMINI_BASE_URL}/models", headers=_gemini_headers(api_key), params={"pageSize": 200})
    except httpx.HTTPError as exc:
        raise _network_error("Google Gemini") from exc
    if resp.status_code != 200:
        raise _gemini_translate(resp)
    try:
        data = resp.json().get("models") or []
    except ValueError as exc:
        raise ProviderError("Gemini respondió algo que no es una lista de modelos.", "bad_request") from exc
    models = []
    for m in data:
        if not isinstance(m, dict):
            continue
        model_id = str(m.get("name", "")).removeprefix("models/")
        if not model_id or "generateContent" not in (m.get("supportedGenerationMethods") or []):
            continue
        if _GEMINI_SKIP.search(model_id):
            continue
        models.append({"id": model_id, "display_name": str(m.get("displayName") or model_id)})
    models.sort(key=lambda m: m["id"])
    return {"models": models, "request_id": None, "organization_id": None, "rate_limits": {}}


def gemini_run(api_key: str, prompt: str, *, context: str, model: str) -> RunResult:
    """POST models/{modelo}:generateContent con instrucciones de sistema y el mensaje del usuario."""
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_content(prompt, context)}]}],
    }
    try:
        with _client(RUN_TIMEOUT) as c:
            resp = c.post(
                f"{GEMINI_BASE_URL}/{_gemini_model_path(model)}:generateContent",
                headers=_gemini_headers(api_key),
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise _network_error("Google Gemini") from exc
    if resp.status_code != 200:
        raise _gemini_translate(resp)
    try:
        body = resp.json()
    except ValueError as exc:
        raise ProviderError("Gemini devolvió una respuesta con un formato inesperado.", "bad_request") from exc
    candidates = body.get("candidates") or []
    feedback = body.get("promptFeedback") or {}
    usage = body.get("usageMetadata") or {}
    refusal = None
    text = ""
    finish = None
    if candidates:
        cand = candidates[0]
        finish = cand.get("finishReason")
        parts = (cand.get("content") or {}).get("parts") or []
        # Las partes marcadas como «thought» son razonamiento interno: no forman parte de la respuesta.
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
            refusal = {"category": finish.lower(), "explanation": None}
    elif feedback.get("blockReason"):
        finish = "BLOCKED"
        refusal = {"category": str(feedback["blockReason"]).lower(), "explanation": feedback.get("blockReasonMessage")}
    return RunResult(
        text=text,
        model=str(body.get("modelVersion") or model),
        stop_reason=finish,
        usage={
            k: v
            for k, v in {
                "input_tokens": usage.get("promptTokenCount"),
                "output_tokens": usage.get("candidatesTokenCount"),
                "thinking_tokens": usage.get("thoughtsTokenCount"),
            }.items()
            if v is not None
        },
        rate_limits={},
        request_id=body.get("responseId"),
        refusal=refusal,
    )


def gemini_count_tokens(api_key: str, text: str, model: str) -> int:
    """POST models/{modelo}:countTokens: recuento exacto del proveedor, sin generar respuesta."""
    try:
        with _client(VERIFY_TIMEOUT) as c:
            resp = c.post(
                f"{GEMINI_BASE_URL}/{_gemini_model_path(model)}:countTokens",
                headers=_gemini_headers(api_key),
                json={"contents": [{"role": "user", "parts": [{"text": text}]}]},
            )
    except httpx.HTTPError as exc:
        raise _network_error("Google Gemini") from exc
    if resp.status_code != 200:
        raise _gemini_translate(resp)
    try:
        return int(resp.json()["totalTokens"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError("Gemini no devolvió un recuento de tokens.", "bad_request") from exc
