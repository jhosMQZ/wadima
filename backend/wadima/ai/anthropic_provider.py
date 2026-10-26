"""Proveedor Anthropic (API oficial de Claude) con la API key propia de cada socio.

- La clave se pasa siempre de forma explícita: el SDK no consulta variables de entorno ni perfiles del
  equipo anfitrión, así que nunca se mezclan cuentas.
- La prueba de conexión usa GET /v1/models (no consume tokens).
- El contexto recuperado de la memoria se entrega como datos delimitados; el modelo no recibe herramientas,
  de modo que el contenido de un documento no puede concederle permisos ni ejecutar acciones.
"""

from __future__ import annotations

from typing import Any

import anthropic
import httpx2

from wadima.ai.common import SYSTEM_PROMPT, ProviderError, RunResult, user_content
from wadima.config import get_settings

__all__ = ["SYSTEM_PROMPT", "ProviderError", "RunResult"]

DEFAULT_MODEL = "claude-opus-5-5"
# Modelos que admiten la recuperación automática del lado del servidor ante una negativa (fallbacks="default").
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Transporte simulado para pruebas (nunca se usa en producción).
TRANSPORT_OVERRIDE: httpx2.BaseTransport | None = None


def client_for(api_key: str) -> anthropic.Anthropic:
    s = get_settings()
    http_client = anthropic.DefaultHttpxClient(transport=TRANSPORT_OVERRIDE) if TRANSPORT_OVERRIDE else None
    return anthropic.Anthropic(
        api_key=api_key, base_url=s.anthropic_base_url, timeout=120.0, max_retries=2, http_client=http_client
    )


def _rate_limits(headers) -> dict[str, str]:
    return {
        k: v for k, v in headers.items() if k.lower().startswith("anthropic-ratelimit-") or k.lower() == "retry-after"
    }


def _translate(exc: Exception) -> ProviderError:
    """Mensajes comprensibles a partir de las excepciones tipadas del SDK (de la más específica a la más general)."""
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError("Anthropic rechazó la API key (no válida o revocada).", "auth")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderError("La API key no tiene permiso para esta operación o modelo.", "permission")
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError("Modelo o recurso no encontrado para esta cuenta.", "not_found")
    if isinstance(exc, anthropic.RateLimitError):
        retry = exc.response.headers.get("retry-after")
        return ProviderError(
            "Límite de uso de la API alcanzado; inténtelo más tarde.",
            "rate_limit",
            int(retry) if retry and retry.isdigit() else None,
        )
    if isinstance(exc, anthropic.BadRequestError):
        return ProviderError(f"Solicitud rechazada por la API: {exc.message}", "bad_request")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return ProviderError(f"Error temporal del servicio de Anthropic ({exc.status_code}).", "server")
        return ProviderError(f"Error de la API ({exc.status_code}): {exc.message}", "bad_request")
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError("No se pudo conectar con api.anthropic.com (red o proxy).", "network")
    return ProviderError(f"Error inesperado: {type(exc).__name__}", "server")


def verify_key(api_key: str) -> dict[str, Any]:
    """Prueba real y gratuita: lista los modelos disponibles para la clave."""
    client = client_for(api_key)
    try:
        raw = client.models.with_raw_response.list(limit=50)
        page = raw.parse()
    except anthropic.AnthropicError as exc:
        raise _translate(exc) from exc
    finally:
        client.close()
    models = [{"id": m.id, "display_name": m.display_name} for m in page.data]
    return {
        "models": models,
        "request_id": raw.headers.get("request-id"),
        "organization_id": raw.headers.get("anthropic-organization-id"),
        "rate_limits": _rate_limits(raw.headers),
    }


def run_message(
    api_key: str,
    prompt: str,
    *,
    context: str = "",
    model: str = DEFAULT_MODEL,
    effort: str = "medium",
    max_tokens: int = 16000,
) -> RunResult:
    content = user_content(prompt, context)
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": effort if effort in EFFORTS else "medium"},
    }
    betas: list[str] = []
    if model in FALLBACK_MODELS:
        params["fallbacks"] = "default"
        betas.append(FALLBACK_BETA)
    client = client_for(api_key)
    try:
        raw = (
            client.beta.messages.with_raw_response.create(betas=betas, **params)
            if betas
            else client.beta.messages.with_raw_response.create(**params)
        )
        message = raw.parse()
    except anthropic.AnthropicError as exc:
        raise _translate(exc) from exc
    finally:
        client.close()
    text = "".join(b.text for b in message.content if b.type == "text")
    usage = message.usage.to_dict() if hasattr(message.usage, "to_dict") else dict(message.usage)
    iterations = usage.get("iterations") or []
    served_by_fallback = any(isinstance(i, dict) and i.get("type") == "fallback_message" for i in iterations)
    refusal = None
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        refusal = {
            "category": getattr(details, "category", None) if details else None,
            "explanation": getattr(details, "explanation", None) if details else None,
        }
    return RunResult(
        text=text,
        model=message.model,
        stop_reason=message.stop_reason,
        usage={
            k: usage.get(k)
            for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
            if usage.get(k) is not None
        },
        rate_limits=_rate_limits(raw.headers),
        request_id=raw.headers.get("request-id"),
        refusal=refusal,
        served_by_fallback=served_by_fallback,
        raw_blocks=[b.type for b in message.content],
    )


def count_tokens(api_key: str, text: str, model: str = DEFAULT_MODEL) -> int:
    """Recuento exacto del proveedor (POST /v1/messages/count_tokens), sin generar respuesta."""
    client = client_for(api_key)
    try:
        resp = client.messages.count_tokens(model=model, messages=[{"role": "user", "content": text}])
    except anthropic.AnthropicError as exc:
        raise _translate(exc) from exc
    finally:
        client.close()
    return resp.input_tokens
