"""Catálogo de proveedores de IA y despacho de las operaciones (verificar, ejecutar, contar tokens).

Cada socio conecta las APIs que quiera con sus propias claves. WADIMA no elige modelos por su cuenta: la lista
sale de la propia cuenta del socio al verificar la clave, y el modelo por defecto se toma de esa lista.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from wadima.ai import anthropic_provider, http_providers
from wadima.ai.common import ProviderError, RunResult

ANTHROPIC = "anthropic_api"
OPENAI = "openai_api"
GEMINI = "gemini_api"
DEEPSEEK = "deepseek_api"
COMPATIBLE = "openai_compatible"


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    label: str
    short: str
    key_placeholder: str
    console_url: str
    console_label: str
    supports_effort: bool
    supports_count_tokens: bool
    needs_base_url: bool
    note: str


CATALOG: dict[str, ProviderInfo] = {
    ANTHROPIC: ProviderInfo(
        key=ANTHROPIC,
        label="Anthropic (Claude)",
        short="Claude",
        key_placeholder="sk-ant-api03-…",
        console_url="https://platform.claude.com/settings/keys",
        console_label="platform.claude.com",
        supports_effort=True,
        supports_count_tokens=True,
        needs_base_url=False,
        note="La suscripción de claude.ai no incluye la API; se factura aparte.",
    ),
    OPENAI: ProviderInfo(
        key=OPENAI,
        label="OpenAI",
        short="OpenAI",
        key_placeholder="sk-…",
        console_url="https://platform.openai.com/api-keys",
        console_label="platform.openai.com",
        supports_effort=False,
        supports_count_tokens=False,
        needs_base_url=False,
        note="La suscripción de ChatGPT no incluye la API; se factura aparte.",
    ),
    GEMINI: ProviderInfo(
        key=GEMINI,
        label="Google Gemini",
        short="Gemini",
        key_placeholder="AIza…",
        console_url="https://aistudio.google.com/apikey",
        console_label="aistudio.google.com",
        supports_effort=False,
        supports_count_tokens=True,
        needs_base_url=False,
        note="Clave de la API de Gemini creada en Google AI Studio.",
    ),
    DEEPSEEK: ProviderInfo(
        key=DEEPSEEK,
        label="DeepSeek",
        short="DeepSeek",
        key_placeholder="sk-…",
        console_url="https://platform.deepseek.com/api_keys",
        console_label="platform.deepseek.com",
        supports_effort=False,
        supports_count_tokens=False,
        needs_base_url=False,
        note="Clave de la plataforma de DeepSeek; se factura por uso con saldo prepagado.",
    ),
    COMPATIBLE: ProviderInfo(
        key=COMPATIBLE,
        label="API compatible con OpenAI",
        short="Compatible",
        key_placeholder="clave del servicio",
        console_url="",
        console_label="",
        supports_effort=False,
        supports_count_tokens=False,
        needs_base_url=True,
        note=(
            "Cualquier servicio con el formato de OpenAI (/models y /chat/completions): OpenRouter, Groq, Mistral, "
            "Together… o un servidor local como Ollama o LM Studio en el equipo de WADIMA."
        ),
    ),
}

DEFAULT_MODEL: dict[str, str] = {ANTHROPIC: anthropic_provider.DEFAULT_MODEL}

# Preferencias para elegir el modelo por defecto, solo entre los que la cuenta devuelve realmente.
_PREFERENCES: dict[str, list[re.Pattern[str]]] = {
    OPENAI: [re.compile(p) for p in (r"^gpt-5(\.\d+)?$", r"^gpt-5", r"^gpt-4\.1$", r"^gpt-4o$", r"^o\d", r"^gpt-")],
    DEEPSEEK: [re.compile(p) for p in (r"^deepseek-chat$", r"^deepseek-")],
    GEMINI: [
        re.compile(p)
        for p in (r"^gemini-[\d.]+-pro$", r"^gemini-[\d.]+-flash$", r"^gemini-.*pro(?!.*(preview|exp))", r"^gemini-")
    ],
}


def info(provider: str) -> ProviderInfo:
    if provider not in CATALOG:
        raise ProviderError("Proveedor de IA desconocido", "bad_request")
    return CATALOG[provider]


def catalog_out() -> list[dict[str, Any]]:
    return [
        {
            "key": p.key,
            "label": p.label,
            "short": p.short,
            "key_placeholder": p.key_placeholder,
            "console_url": p.console_url,
            "console_label": p.console_label,
            "supports_effort": p.supports_effort,
            "supports_count_tokens": p.supports_count_tokens,
            "needs_base_url": p.needs_base_url,
            "note": p.note,
        }
        for p in CATALOG.values()
    ]


def _version_key(model_id: str) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", model_id)]
    return tuple(-n for n in nums) + (model_id,)


def pick_default(provider: str, model_ids: list[str]) -> str:
    """Modelo por defecto: el preferido del proveedor si la cuenta lo tiene; si no, el primero de la lista."""
    if provider in DEFAULT_MODEL and (DEFAULT_MODEL[provider] in model_ids or not model_ids):
        return DEFAULT_MODEL[provider]
    stable = [m for m in model_ids if not re.search(r"preview|exp|latest|lite|nano|mini", m)]
    for pattern in _PREFERENCES.get(provider, []):
        for pool in (stable, model_ids):
            hits = sorted((m for m in pool if pattern.search(m)), key=_version_key)
            if hits:
                return hits[0]
    return sorted(model_ids)[0] if model_ids else ""


# --------------------------------------------------------------------------- validación de claves y direcciones
def validate_key(provider: str, key: str) -> str:
    key = key.strip()
    if any(ch.isspace() for ch in key):
        raise ProviderError("La clave no puede contener espacios", "bad_request")
    if provider != ANTHROPIC and key.startswith("sk-ant-"):
        raise ProviderError("Esa clave es de Anthropic: conéctela en «Anthropic (Claude)»", "bad_request")
    if provider == ANTHROPIC:
        if not key.startswith("sk-ant-"):
            raise ProviderError(
                "Formato de API key no reconocido (debe empezar por «sk-ant-»; se obtiene en "
                "platform.claude.com, no en la suscripción de claude.ai)",
                "bad_request",
            )
        if key.startswith("sk-ant-admin"):
            raise ProviderError("Esa es una clave de administración; use una API key normal", "bad_request")
    elif provider == OPENAI:
        if not key.startswith("sk-"):
            raise ProviderError(
                "Formato de API key no reconocido (las claves de OpenAI empiezan por «sk-»; se crean en "
                "platform.openai.com, no en la suscripción de ChatGPT)",
                "bad_request",
            )
        if key.startswith("sk-admin-"):
            raise ProviderError("Esa es una clave de administración de OpenAI; use una API key normal", "bad_request")
    elif provider == DEEPSEEK:
        if not key.startswith("sk-"):
            raise ProviderError(
                "Formato de API key no reconocido (las claves de DeepSeek empiezan por «sk-»; se crean en "
                "platform.deepseek.com)",
                "bad_request",
            )
    elif provider == GEMINI:
        if len(key) < 20:
            raise ProviderError("La API key de Gemini es demasiado corta", "bad_request")
    elif provider == COMPATIBLE:
        if not key:
            raise ProviderError(
                "Indique la clave del servicio (para Ollama o LM Studio sirve cualquier texto)", "bad_request"
            )
    return key


_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


def _is_internal(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def normalize_base_url(raw: str, *, allow_local: bool) -> str:
    """Dirección de una API compatible con OpenAI. Evita que el servidor de WADIMA se use para alcanzar la red
    interna: HTTPS hacia servicios públicos; HTTP solo hacia el propio equipo (Ollama, LM Studio) y solo para
    administradores."""
    url = raw.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ProviderError(
            "La dirección debe empezar por https:// (por ejemplo https://openrouter.ai/api/v1)", "bad_request"
        )
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ProviderError("La dirección no puede llevar usuario, contraseña, parámetros ni fragmentos", "bad_request")
    host = parts.hostname.lower()
    if host in _LOOPBACK_NAMES:
        if not allow_local:
            raise ProviderError(
                "Solo un administrador puede conectar un servidor local (Ollama, LM Studio) del equipo de WADIMA",
                "permission",
            )
        return url
    if parts.scheme != "https":
        raise ProviderError("Fuera del propio equipo solo se admiten direcciones https://", "bad_request")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addresses = [literal] if literal else []
    if literal is None:
        try:
            infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
        except OSError:
            infos = []  # sin resolución ahora: la petición fallará con un error de red claro
        for info_ in infos:
            try:
                addresses.append(ipaddress.ip_address(info_[4][0]))
            except ValueError:
                continue
    if any(_is_internal(a) for a in addresses):
        raise ProviderError("La dirección apunta a la red interna; solo se admiten servicios públicos", "bad_request")
    return url


# --------------------------------------------------------------------------- operaciones
def verify(provider: str, key: str, base_url: str = "") -> dict[str, Any]:
    if provider == ANTHROPIC:
        return anthropic_provider.verify_key(key)
    if provider == OPENAI:
        return http_providers.openai_verify(key)
    if provider == GEMINI:
        return http_providers.gemini_verify(key)
    if provider == DEEPSEEK:
        return http_providers.openai_verify(key, http_providers.DEEPSEEK_BASE_URL, name="DeepSeek")
    if provider == COMPATIBLE:
        return http_providers.openai_verify(key, base_url, name=urlsplit(base_url).hostname or "el servicio")
    raise ProviderError("Proveedor de IA desconocido", "bad_request")


def run(
    provider: str, key: str, prompt: str, *, context: str, model: str, effort: str = "medium", base_url: str = ""
) -> RunResult:
    if provider == ANTHROPIC:
        return anthropic_provider.run_message(key, prompt, context=context, model=model, effort=effort)
    if provider == OPENAI:
        return http_providers.openai_run(key, prompt, context=context, model=model)
    if provider == GEMINI:
        return http_providers.gemini_run(key, prompt, context=context, model=model)
    if provider == DEEPSEEK:
        return http_providers.openai_run(
            key, prompt, context=context, model=model, base_url=http_providers.DEEPSEEK_BASE_URL, name="DeepSeek"
        )
    if provider == COMPATIBLE:
        return http_providers.openai_run(
            key,
            prompt,
            context=context,
            model=model,
            base_url=base_url,
            name=urlsplit(base_url).hostname or "el servicio",
        )
    raise ProviderError("Proveedor de IA desconocido", "bad_request")


def count_tokens(provider: str, key: str, text: str, model: str) -> int:
    if provider == ANTHROPIC:
        return anthropic_provider.count_tokens(key, text, model)
    if provider == GEMINI:
        return http_providers.gemini_count_tokens(key, text, model)
    raise ProviderError(
        f"{info(provider).label} no ofrece un recuento de tokens sin generar respuesta; WADIMA no inventa estimaciones",
        "bad_request",
    )
