"""Memoria compartida: consulta con referencias, revisión, corrección, archivo, borrado y contradicciones."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from wadima import audit
from wadima.api.serial import iso
from wadima.db import get_db
from wadima.memory import agent as _agent  # noqa: F401 - registra los trabajos del Memory Agent
from wadima.memory.agent import item_snapshot, record_history
from wadima.memory.retrieval import retrieve
from wadima.models import MemoryHistory, MemoryItem, MemoryRelation, User
from wadima.realtime import emit
from wadima.security.auth import Principal, client_ip, now_utc, require_password_ok
from wadima.security.perms import accessible_area_ids, area_role, require_area
from wadima.services import jobs

router = APIRouter(prefix="/api/memory", tags=["memory"])


def item_out(m: MemoryItem) -> dict:
    return {
        "id": str(m.id),
        "layer": m.layer,
        "kind": m.kind,
        "title": m.title,
        "content": m.content,
        "area_id": str(m.area_id),
        "project_id": str(m.project_id) if m.project_id else None,
        "source_type": m.source_type,
        "source_id": m.source_id,
        "source_version": m.source_version,
        "source_ref": m.source_ref,
        "confidence": m.confidence,
        "validation": m.validation,
        "status": m.status,
        "valid_from": iso(m.valid_from),
        "valid_until": iso(m.valid_until),
        "created_by_agent": m.created_by_agent,
        "created_at": iso(m.created_at),
        "updated_at": iso(m.updated_at),
        "version": m.version,
    }


def _item(db: Session, principal: Principal, item_id: uuid.UUID, min_role: str = "viewer") -> MemoryItem:
    m = db.get(MemoryItem, item_id)
    if m is None or m.status == "deleted":
        raise HTTPException(404, "Recuerdo no encontrado")
    require_area(db, principal, m.area_id, min_role)
    return m


class QueryIn(BaseModel):
    q: str = Field(min_length=2, max_length=500)
    area_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    limit: int = Field(default=8, ge=1, le=30)
    token_budget: int = Field(default=3000, ge=200, le=20000)


@router.post("/query")
def query(body: QueryIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    """Recupera contexto pertinente solo de las áreas a las que el usuario tiene acceso."""
    ids = accessible_area_ids(db, principal)
    if body.area_id:
        if body.area_id not in ids:
            raise HTTPException(404, "Área no encontrada")
        ids = [body.area_id]
    return retrieve(db, ids, body.q, project_id=body.project_id, limit=body.limit, token_budget=body.token_budget)


@router.get("/items")
def list_items(
    area_id: uuid.UUID | None = None,
    layer: str | None = None,
    kind: str | None = None,
    status: str | None = "active",
    validation: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    ids = accessible_area_ids(db, principal)
    if area_id:
        if area_id not in ids:
            raise HTTPException(404, "Área no encontrada")
        ids = [area_id]
    if not ids:
        return {"items": [], "total": 0, "counts": {}}
    base = select(MemoryItem).where(MemoryItem.area_id.in_(ids), MemoryItem.status != "deleted")
    if layer:
        base = base.where(MemoryItem.layer == layer)
    if kind:
        base = base.where(MemoryItem.kind == kind)
    if status:
        base = base.where(MemoryItem.status == status)
    if validation:
        base = base.where(MemoryItem.validation == validation)
    if q:
        like = f"%{q.lower()}%"
        base = base.where(or_(func.lower(MemoryItem.title).like(like), func.lower(MemoryItem.content).like(like)))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = db.execute(base.order_by(MemoryItem.updated_at.desc()).limit(min(limit, 300)).offset(offset)).scalars()
    counts = dict(
        db.execute(
            select(MemoryItem.layer, func.count())
            .where(MemoryItem.area_id.in_(ids), MemoryItem.status == "active")
            .group_by(MemoryItem.layer)
        ).all()
    )
    return {"items": [item_out(m) for m in rows], "total": total, "counts": counts}


@router.get("/items/{item_id}")
def get_item(item_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    m = _item(db, principal, item_id)
    rels = db.execute(
        select(MemoryRelation, MemoryItem)
        .join(
            MemoryItem, MemoryItem.id == func.coalesce(func.nullif(MemoryRelation.to_id, m.id), MemoryRelation.from_id)
        )
        .where(or_(MemoryRelation.from_id == m.id, MemoryRelation.to_id == m.id))
    ).all()
    history = db.execute(
        select(MemoryHistory, User.display_name)
        .outerjoin(User, User.id == MemoryHistory.actor_id)
        .where(MemoryHistory.memory_id == m.id)
        .order_by(MemoryHistory.id.desc())
    ).all()
    return {
        **item_out(m),
        "relations": [
            {
                "id": str(r.id),
                "relation": r.relation,
                "score": r.score,
                "detail": r.detail,
                "resolved": r.resolved,
                "resolution_note": r.resolution_note,
                "other": {"id": str(o.id), "title": o.title, "status": o.status, "content": o.content[:300]},
            }
            for r, o in rels
        ],
        "history": [
            {
                "version": h.version,
                "change": h.change,
                "before": h.before,
                "after": h.after,
                "reason": h.reason,
                "actor": name or h.actor_agent,
                "ts": iso(h.ts),
            }
            for h, name in history
        ],
    }


class ItemPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1, max_length=50000)
    validation: str | None = Field(default=None, pattern="^(unverified|inferred|confirmed|rejected)$")
    status: str | None = Field(default=None, pattern="^(active|obsolete|archived)$")
    valid_until: datetime | None = None
    clear_valid_until: bool = False
    reason: str = Field(default="", max_length=2000)


def _require_reviewer(db: Session, principal: Principal, m: MemoryItem) -> None:
    """Revisan administradores y propietarios del área de origen."""
    if not principal.is_admin and area_role(db, principal, m.area_id) != "owner":
        raise HTTPException(403, "Solo administradores o propietarios del área pueden revisar la memoria")


@router.patch("/items/{item_id}")
def correct_item(
    item_id: uuid.UUID,
    body: ItemPatch,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    m = _item(db, principal, item_id)
    _require_reviewer(db, principal, m)
    before = item_snapshot(m)
    changes = body.model_dump(exclude_none=True, exclude={"reason", "clear_valid_until"})
    for k, v in changes.items():
        setattr(m, k, v)
    if body.clear_valid_until:
        m.valid_until = None
    if body.content is not None and m.validation == "inferred" and body.validation is None:
        m.validation = "confirmed"  # un humano corrigió y validó el contenido
    m.version += 1
    change = "archive" if body.status == "archived" else "validate" if body.validation else "correct"
    record_history(db, m, change, before, actor_id=principal.id, reason=body.reason)
    audit.record(
        db,
        "memory.corrected",
        actor_id=principal.id,
        target_type="memory_item",
        target_id=m.id,
        area_id=m.area_id,
        ip=client_ip(request),
        data={"fields": sorted(changes)},
    )
    emit(db, "memory.changed", area_id=m.area_id, id=str(m.id))
    jobs.enqueue(
        db,
        "memory.consolidate",
        {"area_id": str(m.area_id)},
        dedupe_key=f"memory:consolidate:{m.area_id}",
        delay_seconds=30,
    )
    db.commit()
    return item_out(m)


@router.delete("/items/{item_id}")
def delete_item(
    item_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    m = _item(db, principal, item_id)
    if not principal.is_admin:
        raise HTTPException(403, "Solo un administrador puede eliminar recuerdos")
    before = item_snapshot(m)
    record_history(db, m, "delete", before, actor_id=principal.id, reason="Eliminado por un administrador")
    audit.record(
        db,
        "memory.deleted",
        actor_id=principal.id,
        target_type="memory_item",
        target_id=m.id,
        area_id=m.area_id,
        ip=client_ip(request),
        data={"title": m.title[:200]},
    )
    area_id = m.area_id
    db.delete(m)
    emit(db, "memory.changed", area_id=area_id)
    db.commit()
    return {"ok": True}


@router.get("/contradictions")
def contradictions(
    area_id: uuid.UUID | None = None,
    include_resolved: bool = False,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    ids = accessible_area_ids(db, principal)
    if area_id:
        ids = [a for a in ids if a == area_id]
    if not ids:
        return []
    q = (
        select(MemoryRelation)
        .join(MemoryItem, MemoryItem.id == MemoryRelation.from_id)
        .where(MemoryRelation.relation == "contradicts", MemoryItem.area_id.in_(ids))
    )
    if not include_resolved:
        q = q.where(MemoryRelation.resolved.is_(False))
    return [
        {
            "id": str(rel.id),
            "detail": rel.detail,
            "resolved": rel.resolved,
            "resolution_note": rel.resolution_note,
            "created_at": iso(rel.created_at),
            "a": _brief(db, rel.from_id),
            "b": _brief(db, rel.to_id),
        }
        for rel in db.execute(q.order_by(MemoryRelation.created_at.desc()).limit(200)).scalars()
    ]


def _brief(db: Session, item_id: uuid.UUID) -> dict:
    m = db.get(MemoryItem, item_id)
    return {
        "id": str(m.id),
        "title": m.title,
        "content": m.content[:400],
        "status": m.status,
        "source_ref": m.source_ref,
        "source_type": m.source_type,
        "validation": m.validation,
        "created_at": iso(m.created_at),
    }


class ResolveIn(BaseModel):
    note: str = Field(min_length=3, max_length=2000)
    archive_item_id: uuid.UUID | None = None


@router.post("/contradictions/{relation_id}/resolve")
def resolve_contradiction(
    relation_id: uuid.UUID,
    body: ResolveIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    rel = db.get(MemoryRelation, relation_id)
    if rel is None or rel.relation != "contradicts":
        raise HTTPException(404, "Contradicción no encontrada")
    first = _item(db, principal, rel.from_id)
    _require_reviewer(db, principal, first)
    if body.archive_item_id:
        if body.archive_item_id not in (rel.from_id, rel.to_id):
            raise HTTPException(422, "Solo se puede archivar uno de los dos recuerdos implicados")
        target = db.get(MemoryItem, body.archive_item_id)
        before = item_snapshot(target)
        target.status = "archived"
        target.valid_until = now_utc()
        target.version += 1
        record_history(db, target, "archive", before, actor_id=principal.id, reason=f"Contradicción: {body.note}")
    rel.resolved = True
    rel.resolved_by = principal.id
    rel.resolution_note = body.note
    audit.record(
        db,
        "memory.contradiction_resolved",
        actor_id=principal.id,
        target_type="memory_relation",
        target_id=rel.id,
        area_id=first.area_id,
        ip=client_ip(request),
        data={"archived": str(body.archive_item_id) if body.archive_item_id else None},
    )
    emit(db, "memory.changed", area_id=first.area_id)
    db.commit()
    return {"ok": True}


@router.get("/stats")
def stats(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    ids = accessible_area_ids(db, principal)
    if not ids:
        return {"by_layer": {}, "by_status": {}, "by_validation": {}, "open_contradictions": 0}

    def by(col):
        return dict(db.execute(select(col, func.count()).where(MemoryItem.area_id.in_(ids)).group_by(col)).all())

    open_c = db.execute(
        select(func.count())
        .select_from(MemoryRelation)
        .join(MemoryItem, MemoryItem.id == MemoryRelation.from_id)
        .where(
            MemoryRelation.relation == "contradicts", MemoryRelation.resolved.is_(False), MemoryItem.area_id.in_(ids)
        )
    ).scalar_one()
    return {
        "by_layer": by(MemoryItem.layer),
        "by_status": by(MemoryItem.status),
        "by_validation": by(MemoryItem.validation),
        "open_contradictions": open_c,
    }
