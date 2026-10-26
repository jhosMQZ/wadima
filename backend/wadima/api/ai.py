"""Conexiones de cada socio con la IA (Claude, OpenAI, Gemini, DeepSeek o APIs compatibles) y ejecuciones.

Cada socio usa sus propias API keys. Las claves se guardan cifradas, nunca se
devuelven al cliente y nunca se usan para peticiones de otro socio. Las ejecuciones son privadas de su
autor hasta que este las comparte explícitamente con un área o proyecto.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from wadima import audit
from wadima.activity import log_activity
from wadima.ai import providers
from wadima.api.serial import iso
from wadima.config import get_settings
from wadima.db import get_db
from wadima.memory.retrieval import retrieve
from wadima.models import AICredential, AIRun, DocumentVersion, MCPToken, Project, User
from wadima.realtime import emit
from wadima.security.auth import Principal, client_ip, now_utc, require_password_ok
from wadima.security.crypto import decrypt_secret, encrypt_secret, random_token, sha256_hex
from wadima.security.perms import accessible_area_ids, require_area
from wadima.services import jobs

router = APIRouter(prefix="/api/ai", tags=["ai"])

PROVIDER = providers.ANTHROPIC  # proveedor por defecto (compatibilidad con clientes anteriores)


_OTHER_LABELS = {"claude_mcp": "Claude (suscripción vía MCP)", "manual_import": "un resultado importado"}


def _label(provider: str) -> str:
    if provider in providers.CATALOG:
        return providers.CATALOG[provider].label
    return _OTHER_LABELS.get(provider, provider)


def cred_out(c: AICredential | None) -> dict | None:
    if c is None:
        return None
    return {
        "provider": c.provider,
        "provider_label": _label(c.provider),
        "label": c.label,
        "key_hint": c.key_hint,
        "status": c.status,
        "base_url": c.base_url or None,
        "default_model": c.default_model or providers.DEFAULT_MODEL.get(c.provider, ""),
        "models": (c.account_info or {}).get("models", []),
        "organization_id": (c.account_info or {}).get("organization_id"),
        "rate_limits": c.rate_limits or {},
        "verified_at": iso(c.verified_at),
        "last_error": c.last_error,
        "updated_at": iso(c.updated_at),
    }


def run_out(r: AIRun, include_prompt: bool = True) -> dict:
    return {
        "id": str(r.id),
        "user_id": str(r.user_id),
        "provider": r.provider,
        "provider_label": _label(r.provider),
        "model": r.model,
        "prompt": r.prompt if include_prompt else r.prompt[:200],
        "context_refs": r.context_refs,
        "output": r.output,
        "usage": r.usage,
        "status": r.status,
        "error": r.error,
        "shared": r.shared,
        "area_id": str(r.area_id) if r.area_id else None,
        "project_id": str(r.project_id) if r.project_id else None,
        "created_at": iso(r.created_at),
        "finished_at": iso(r.finished_at),
    }


def _check_provider(provider: str) -> str:
    if provider not in providers.CATALOG:
        raise HTTPException(422, "Proveedor de IA desconocido")
    return provider


def _my_cred(db: Session, principal: Principal, provider: str = PROVIDER) -> AICredential | None:
    return db.execute(
        select(AICredential).where(AICredential.user_id == principal.id, AICredential.provider == provider)
    ).scalar_one_or_none()


def _my_creds(db: Session, principal: Principal) -> list[AICredential]:
    rows = db.execute(select(AICredential).where(AICredential.user_id == principal.id)).scalars().all()
    order = list(providers.CATALOG)
    return sorted((c for c in rows if c.provider in providers.CATALOG), key=lambda c: order.index(c.provider))


def _usable_cred(db: Session, principal: Principal, provider: str | None) -> AICredential:
    """Credencial verificada del proveedor pedido; sin proveedor, Claude si está verificado o la primera verificada."""
    if provider:
        cred = _my_cred(db, principal, _check_provider(provider))
        if cred is None or cred.status != "verified" or not cred.secret_encrypted:
            raise HTTPException(400, f"Conecte y verifique primero su propia API key de {_label(provider)}")
        return cred
    usable = [c for c in _my_creds(db, principal) if c.status == "verified" and c.secret_encrypted]
    if not usable:
        raise HTTPException(400, "Conecte y verifique primero su propia API key de Anthropic u otro proveedor")
    return usable[0]


def _apply_verification(cred: AICredential, api_key: str) -> None:
    try:
        result = providers.verify(cred.provider, api_key, cred.base_url)
    except providers.ProviderError as exc:
        cred.status = "error"
        cred.last_error = str(exc)
        return
    cred.status = "verified"
    cred.verified_at = now_utc()
    cred.last_error = ""
    cred.account_info = {"models": result["models"], "organization_id": result["organization_id"]}
    cred.rate_limits = result["rate_limits"]
    model_ids = [m["id"] for m in result["models"]]
    if not cred.default_model or (model_ids and cred.default_model not in model_ids):
        cred.default_model = providers.pick_default(cred.provider, model_ids)


# --------------------------------------------------------------------------- credenciales
@router.get("/credentials")
def my_credentials(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    users = db.execute(select(User).where(User.is_active).order_by(User.created_at, User.username)).scalars().all()
    all_creds = db.execute(select(AICredential)).scalars().all()
    claude = {c.user_id: c for c in all_creds if c.provider == PROVIDER}
    verified: dict[uuid.UUID, list[str]] = {}
    for c in all_creds:
        if c.status == "verified" and c.provider in providers.CATALOG:
            verified.setdefault(c.user_id, []).append(providers.CATALOG[c.provider].short)
    return {
        # «mine» se mantiene por compatibilidad: es la conexión con Claude (Anthropic).
        "mine": cred_out(claude.get(principal.id)),
        "providers": [cred_out(c) for c in _my_creds(db, principal) if c.status != "revoked"],
        "catalog": providers.catalog_out(),
        # De los demás socios solo se muestra el estado, nunca datos de su cuenta.
        "partners": [
            {
                "user_id": str(u.id),
                "display_name": u.display_name,
                "status": claude[u.id].status if u.id in claude else "not_configured",
                "apis": sorted(verified.get(u.id, [])),
            }
            for u in users
        ],
    }


class CredentialIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=400)
    label: str = Field(default="", max_length=80)
    provider: str = Field(default=PROVIDER, max_length=30)
    base_url: str = Field(default="", max_length=300)


@router.put("/credentials")
def set_credential(
    body: CredentialIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    provider = _check_provider(body.provider)
    meta = providers.CATALOG[provider]
    if provider != providers.COMPATIBLE and len(body.api_key.strip()) < 20:
        raise HTTPException(422, "La API key es demasiado corta")
    try:
        key = providers.validate_key(provider, body.api_key)
        base_url = (
            providers.normalize_base_url(body.base_url, allow_local=principal.is_admin)
            if meta.needs_base_url
            else ""
        )
    except providers.ProviderError as exc:
        raise HTTPException(403 if exc.kind == "permission" else 422, str(exc)) from exc
    cred = _my_cred(db, principal, provider)
    if cred is None:
        cred = AICredential(user_id=principal.id, provider=provider)
        db.add(cred)
    if (cred.base_url or "") != base_url:
        cred.default_model = ""  # otro servicio: otra lista de modelos
    cred.base_url = base_url
    cred.secret_encrypted = encrypt_secret(key)
    cred.key_hint = f"…{key[-4:]}" if len(key) >= 8 else "…"
    cred.label = body.label
    cred.status = "unverified"
    _apply_verification(cred, key)
    audit.record(
        db,
        "ai.credential_set",
        actor_id=principal.id,
        target_type="ai_credential",
        ip=client_ip(request),
        data={"provider": provider, "key_hint": cred.key_hint, "status": cred.status, "base_url": base_url or None},
    )
    emit(db, "ai.changed", everyone=True)
    db.commit()
    return cred_out(cred)


@router.post("/credentials/test")
def test_credential(
    provider: str = PROVIDER, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    cred = _my_cred(db, principal, _check_provider(provider))
    if cred is None or not cred.secret_encrypted:
        raise HTTPException(400, f"No ha configurado una API key de {_label(provider)}")
    _apply_verification(cred, decrypt_secret(cred.secret_encrypted))
    emit(db, "ai.changed", everyone=True)
    db.commit()
    return cred_out(cred)


class CredentialPatch(BaseModel):
    default_model: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=80)
    provider: str = Field(default=PROVIDER, max_length=30)


@router.patch("/credentials")
def update_credential(
    body: CredentialPatch, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    cred = _my_cred(db, principal, _check_provider(body.provider))
    if cred is None:
        raise HTTPException(404, "No hay conexión configurada")
    if body.default_model is not None:
        available = {m["id"] for m in (cred.account_info or {}).get("models", [])}
        if available and body.default_model not in available:
            raise HTTPException(422, "Ese modelo no está disponible para su cuenta")
        cred.default_model = body.default_model
    if body.label is not None:
        cred.label = body.label
    db.commit()
    return cred_out(cred)


@router.delete("/credentials")
def delete_credential(
    request: Request,
    provider: str = PROVIDER,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    cred = _my_cred(db, principal, _check_provider(provider))
    if cred is None:
        raise HTTPException(404, "No hay conexión configurada")
    hint = cred.key_hint
    cred.secret_encrypted = None
    cred.status = "revoked"
    cred.account_info = {}
    cred.rate_limits = {}
    audit.record(
        db,
        "ai.credential_removed",
        actor_id=principal.id,
        target_type="ai_credential",
        ip=client_ip(request),
        data={"provider": provider, "key_hint": hint},
    )
    emit(db, "ai.changed", everyone=True)
    db.commit()
    meta = providers.CATALOG[provider]
    if provider == PROVIDER:
        note = (
            "La clave se borró de WADIMA. Para invalidarla en Anthropic, revóquela en platform.claude.com → API keys."
        )
    elif meta.console_label:
        note = f"La clave se borró de WADIMA. Para invalidarla en {meta.label}, revóquela en {meta.console_label}."
    else:
        note = "La clave se borró de WADIMA. Para invalidarla, revóquela también en el panel del servicio."
    return {"ok": True, "note": note}


# --------------------------------------------------------------------------- ejecuciones
class RunIn(BaseModel):
    prompt: str = Field(min_length=2, max_length=20000)
    area_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    use_memory: bool = True
    provider: str | None = Field(default=None, max_length=30)
    model: str | None = Field(default=None, max_length=200)
    effort: str = Field(default="medium", pattern="^(low|medium|high|xhigh|max)$")
    context_budget: int = Field(default=3000, ge=200, le=20000)


@router.post("/runs")
def create_run(body: RunIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    cred = _usable_cred(db, principal, body.provider)
    area_ids = accessible_area_ids(db, principal)
    if body.area_id:
        require_area(db, principal, body.area_id)
        area_ids = [body.area_id]
    if body.project_id:
        p = db.get(Project, body.project_id)
        if p is None or p.area_id not in accessible_area_ids(db, principal):
            raise HTTPException(404, "Proyecto no encontrado")
    if body.model:
        available = {m["id"] for m in (cred.account_info or {}).get("models", [])}
        if available and body.model not in available:
            raise HTTPException(422, f"Ese modelo no está disponible en su cuenta de {_label(cred.provider)}")
    retrieved = {"items": [], "context": "", "token_estimate": 0, "contradictions": []}
    if body.use_memory:
        retrieved = retrieve(
            db, area_ids, body.prompt, project_id=body.project_id, limit=8, token_budget=body.context_budget
        )
    model = body.model or cred.default_model or providers.DEFAULT_MODEL.get(cred.provider, "")
    if not model:
        raise HTTPException(400, f"Elija un modelo para {_label(cred.provider)} (pruebe la conexión de nuevo)")
    refs = [
        {
            "n": i + 1,
            "type": it["type"],
            "title": it["title"],
            "reference": it["reference"],
            "suspicious": it["suspicious"],
        }
        for i, it in enumerate(retrieved["items"])
    ]
    run = AIRun(
        user_id=principal.id,
        credential_id=cred.id,
        area_id=body.area_id,
        project_id=body.project_id,
        provider=cred.provider,
        model=model,
        prompt=body.prompt,
        context_refs=refs,
        status="running",
    )
    db.add(run)
    db.commit()
    api_key = decrypt_secret(cred.secret_encrypted)
    try:
        result = providers.run(
            cred.provider,
            api_key,
            body.prompt,
            context=retrieved["context"],
            model=model,
            effort=body.effort,
            base_url=cred.base_url,
        )
    except providers.ProviderError as exc:
        run.status = "error"
        run.error = str(exc)
        run.finished_at = now_utc()
        if exc.kind == "auth":
            cred.status = "error"
            cred.last_error = str(exc)
            emit(db, "ai.changed", everyone=True)
        db.commit()
        status = 429 if exc.kind == "rate_limit" else 502 if exc.kind in ("network", "server") else 400
        raise HTTPException(
            status, {"message": str(exc), "kind": exc.kind, "run_id": str(run.id), "retry_after": exc.retry_after}
        ) from exc
    run.output = result.text
    run.model = (result.model or model)[:200]
    run.usage = {
        **result.usage,
        "context_token_estimate": retrieved["token_estimate"],
        "stop_reason": result.stop_reason,
        "request_id": result.request_id,
        "served_by_fallback": result.served_by_fallback,
    }
    run.status = "refused" if result.refusal else "done"
    if result.refusal:
        run.error = f"El modelo declinó la petición ({result.refusal.get('category') or 'sin categoría'})"
    run.finished_at = now_utc()
    cred.rate_limits = result.rate_limits or cred.rate_limits
    db.commit()
    return {**run_out(run), "contradictions": retrieved["contradictions"], "rate_limits": result.rate_limits}


@router.get("/runs")
def list_runs(limit: int = 50, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    ids = accessible_area_ids(db, principal)
    q = select(AIRun).where(or_(AIRun.user_id == principal.id, (AIRun.shared.is_(True)) & (AIRun.area_id.in_(ids))))
    rows = db.execute(q.order_by(AIRun.created_at.desc()).limit(min(limit, 200))).scalars()
    return [run_out(r, include_prompt=False) | {"mine": r.user_id == principal.id} for r in rows]


def _visible_run(db: Session, principal: Principal, run_id: uuid.UUID) -> AIRun:
    r = db.get(AIRun, run_id)
    if r is None:
        raise HTTPException(404, "Ejecución no encontrada")
    if r.user_id != principal.id and not (r.shared and r.area_id in accessible_area_ids(db, principal)):
        raise HTTPException(404, "Ejecución no encontrada")
    return r


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    r = _visible_run(db, principal, run_id)
    author = db.get(User, r.user_id)
    return run_out(r) | {"mine": r.user_id == principal.id, "author": author.display_name if author else None}


class ShareIn(BaseModel):
    area_id: uuid.UUID
    project_id: uuid.UUID | None = None


@router.post("/runs/{run_id}/share")
def share_run(
    run_id: uuid.UUID, body: ShareIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    r = db.get(AIRun, run_id)
    if r is None or r.user_id != principal.id:
        raise HTTPException(404, "Ejecución no encontrada")
    if r.status != "done":
        raise HTTPException(409, "Solo se pueden compartir ejecuciones completadas")
    require_area(db, principal, body.area_id, "editor")
    if body.project_id:
        p = db.get(Project, body.project_id)
        if p is None or p.area_id != body.area_id:
            raise HTTPException(422, "El proyecto no pertenece al área")
    r.shared = True
    r.area_id = body.area_id
    r.project_id = body.project_id
    log_activity(
        db,
        "ai.result",
        f"Compartió un resultado de {_label(r.provider)}: {r.prompt[:120]}",
        user=principal.user,
        area_id=body.area_id,
        project_id=body.project_id,
        ref_type="ai_run",
        ref_id=r.id,
    )
    jobs.enqueue(
        db, "memory.ingest", {"source_type": "ai_run", "source_id": str(r.id)}, dedupe_key=f"memory:ai_run:{r.id}"
    )
    db.commit()
    return run_out(r)


class ImportIn(BaseModel):
    prompt: str = Field(min_length=2, max_length=20000)
    output: str = Field(min_length=2, max_length=100000)
    source: str = Field(default="claude.ai", max_length=60)


@router.post("/runs/import")
def import_result(body: ImportIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    """Modo compatible: el socio pega un resultado obtenido en la interfaz oficial (claude.ai, Claude Code...).
    Se marca como importado manualmente; WADIMA no accede a la cuenta personal."""
    r = AIRun(
        user_id=principal.id,
        provider="manual_import",
        model=body.source,
        prompt=body.prompt,
        output=body.output,
        status="done",
        usage={"imported": True},
        finished_at=now_utc(),
    )
    db.add(r)
    db.commit()
    return run_out(r)


# --------------------------------------------------------------------------- tokens y herramientas locales
class CountIn(BaseModel):
    text: str | None = Field(default=None, max_length=2_000_000)
    document_version_id: uuid.UUID | None = None
    model: str | None = Field(default=None, max_length=200)
    provider: str | None = Field(default=None, max_length=30)


@router.post("/count-tokens")
def count(body: CountIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    """Medición real del proveedor (sin generar respuesta). Para un documento compara original y Markdown.
    Solo Anthropic y Gemini ofrecen este recuento; con los demás se informa en lugar de estimar."""
    if body.provider:
        cred = _usable_cred(db, principal, body.provider)
    else:
        countable = [
            c
            for c in _my_creds(db, principal)
            if c.status == "verified" and c.secret_encrypted and providers.CATALOG[c.provider].supports_count_tokens
        ]
        if not countable:
            raise HTTPException(
                400, "Se necesita una API key verificada de Anthropic o Gemini para medir tokens con el proveedor"
            )
        cred = countable[0]
    key = decrypt_secret(cred.secret_encrypted)
    model = body.model or cred.default_model or providers.DEFAULT_MODEL.get(cred.provider, "")
    try:
        if body.document_version_id:
            v = db.get(DocumentVersion, body.document_version_id)
            if v is None:
                raise HTTPException(404, "Versión no encontrada")
            from wadima.models import Document

            doc = db.get(Document, v.document_id)
            require_area(db, principal, doc.area_id)
            original = providers.count_tokens(cred.provider, key, v.plain_text or "", model) if v.plain_text else 0
            markdown = providers.count_tokens(cred.provider, key, v.markdown or "", model) if v.markdown else 0
            report = dict(v.report or {})
            report["token_measurement"] = {
                "provider": cred.provider,
                "model": model,
                "original": original,
                "markdown": markdown,
                "measured_at": now_utc().isoformat(),
                "method": "count_tokens",
            }
            v.report = report
            db.commit()
            return report["token_measurement"]
        if not body.text:
            raise HTTPException(422, "Indique un texto o una versión de documento")
        return {
            "provider": cred.provider,
            "model": model,
            "input_tokens": providers.count_tokens(cred.provider, key, body.text, model),
            "method": "count_tokens",
        }
    except providers.ProviderError as exc:
        raise HTTPException(400 if exc.kind != "rate_limit" else 429, str(exc)) from exc


@router.get("/local-tools")
def local_tools(principal: Principal = Depends(require_password_ok)):
    """Informa de herramientas instaladas en el servidor. WADIMA no reutiliza sus sesiones: pertenecen al
    usuario del equipo anfitrión y no pueden compartirse con otros socios."""
    path = shutil.which("claude")
    version = None
    if path:
        try:
            version = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            version = None
    return {
        "claude_code": {
            "installed": bool(path),
            "version": version,
            "used_by_wadima": False,
            "note": "Claude Code usa la sesión personal de quien lo instaló; WADIMA no la reutiliza.",
        }
    }


# --------------------------------------------------------------------------- suscripción de Claude vía MCP
MCP_PROVIDER = "claude_mcp"
MCP_ACTIVE_DAYS = 14


def token_out(t: MCPToken) -> dict:
    return {
        "id": str(t.id),
        "name": t.name,
        "hint": t.token_hint,
        "created_at": iso(t.created_at),
        "last_used_at": iso(t.last_used_at),
        "revoked_at": iso(t.revoked_at),
        "calls": t.calls,
        "client": t.last_client or {},
    }


def _mcp_url() -> str:
    return get_settings().public_origin.rstrip("/") + "/mcp"


@router.get("/options")
def options(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    """Opciones de integración con la IA y su estado real para este socio."""
    cred = _my_cred(db, principal)
    mine = {c.provider: c for c in _my_creds(db, principal)}
    tokens = (
        db.execute(
            select(MCPToken)
            .where(MCPToken.user_id == principal.id, MCPToken.revoked_at.is_(None))
            .order_by(MCPToken.created_at.desc())
        )
        .scalars()
        .all()
    )
    used = [t for t in tokens if t.last_used_at]
    last = max(used, key=lambda t: t.last_used_at) if used else None
    recent = bool(last and (now_utc() - last.last_used_at).days < MCP_ACTIVE_DAYS)
    if recent:
        status = "connected"
    elif last:
        status = "used_before"
    else:
        status = "tokens_only" if tokens else "not_configured"
    return {
        "api_key": {
            "status": cred.status if cred else "not_configured",
            "verified_at": iso(cred.verified_at) if cred else None,
            "key_hint": cred.key_hint if cred else None,
        },
        "subscription_mcp": {
            "enabled": get_settings().mcp_enabled,
            "url": _mcp_url(),
            # «Conectado» solo cuando un cliente MCP ha hecho una petición real con un token vigente.
            "status": status,
            "last_used_at": iso(last.last_used_at) if last else None,
            "client": (last.last_client or {}) if last else {},
            "tokens": [token_out(t) for t in tokens],
        },
        "manual_import": {"status": "available"},
        # Otras APIs de IA con clave propia (OpenAI, Gemini, DeepSeek, compatibles con OpenAI).
        "other_apis": [
            {
                "provider": key,
                "label": meta.label,
                "status": mine[key].status if key in mine else "not_configured",
                "key_hint": mine[key].key_hint if key in mine else None,
            }
            for key, meta in providers.CATALOG.items()
            if key != PROVIDER
        ],
    }


@router.get("/agents")
def agents(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    """IA de cada socio: a quién se le pueden asignar tareas (Claude conectado por MCP) y su estado."""
    from wadima import ai_tasks

    users = db.execute(select(User).where(User.is_active).order_by(User.created_at, User.username)).scalars().all()
    status = ai_tasks.claude_status(db, list(users))
    return [{"user_id": str(u.id), "owner_name": u.display_name, **status[u.id]} for u in users]


class TokenIn(BaseModel):
    name: str = Field(default="Claude Code", min_length=1, max_length=80)


@router.post("/mcp-tokens", status_code=201)
def create_mcp_token(
    body: TokenIn, request: Request, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    if not get_settings().mcp_enabled:
        raise HTTPException(409, "El servidor MCP está deshabilitado en esta instalación")
    active = (
        db.execute(select(MCPToken).where(MCPToken.user_id == principal.id, MCPToken.revoked_at.is_(None)))
        .scalars()
        .all()
    )
    if len(active) >= 5:
        raise HTTPException(409, "Máximo 5 tokens activos; revoque alguno antes de crear otro")
    raw = "wmcp_" + random_token(32)
    tok = MCPToken(user_id=principal.id, name=body.name.strip(), token_hash=sha256_hex(raw), token_hint=f"…{raw[-4:]}")
    db.add(tok)
    db.flush()
    audit.record(
        db,
        "ai.mcp_token_created",
        actor_id=principal.id,
        target_type="mcp_token",
        target_id=tok.id,
        ip=client_ip(request),
        data={"name": tok.name, "hint": tok.token_hint},
    )
    emit(db, "ai.changed", audience={principal.id})
    db.commit()
    # El token completo solo se muestra en esta respuesta.
    return {**token_out(tok), "token": raw, "url": _mcp_url()}


@router.delete("/mcp-tokens/{token_id}")
def revoke_mcp_token(
    token_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    tok = db.get(MCPToken, token_id)
    if tok is None or tok.user_id != principal.id:
        raise HTTPException(404, "Token no encontrado")
    if tok.revoked_at is None:
        tok.revoked_at = now_utc()
        audit.record(
            db,
            "ai.mcp_token_revoked",
            actor_id=principal.id,
            target_type="mcp_token",
            target_id=tok.id,
            ip=client_ip(request),
            data={"hint": tok.token_hint},
        )
        emit(db, "ai.changed", audience={principal.id})
        db.commit()
    return token_out(tok)


class TaskIn(BaseModel):
    prompt: str = Field(min_length=2, max_length=20000)
    area_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    use_memory: bool = True


@router.post("/tasks", status_code=201)
def create_task(body: TaskIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    """Encarga una tarea a Claude con la suscripción del propio socio: queda en cola hasta que su Claude Code o
    Claude Desktop la recoja por MCP (list_pending_tasks → get_task → submit_result)."""
    if body.area_id:
        require_area(db, principal, body.area_id)
    if body.project_id:
        p = db.get(Project, body.project_id)
        if p is None or p.area_id not in accessible_area_ids(db, principal):
            raise HTTPException(404, "Proyecto no encontrado")
    run = AIRun(
        user_id=principal.id,
        area_id=body.area_id,
        project_id=body.project_id,
        provider=MCP_PROVIDER,
        model="suscripción (se elige en Claude)",
        prompt=body.prompt,
        status="queued",
        usage={"use_memory": body.use_memory},
    )
    db.add(run)
    emit(db, "ai.changed", audience={principal.id})
    db.commit()
    return run_out(run)


@router.post("/tasks/{run_id}/cancel")
def cancel_task(run_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    run = db.get(AIRun, run_id)
    if run is None or run.user_id != principal.id or run.provider != MCP_PROVIDER:
        raise HTTPException(404, "Tarea no encontrada")
    if run.status not in ("queued", "claimed"):
        raise HTTPException(409, "La tarea ya terminó")
    run.status = "cancelled"
    run.finished_at = now_utc()
    emit(db, "ai.changed", audience={principal.id})
    db.commit()
    return run_out(run)
