"""Memory Agent: organiza el conocimiento persistente a partir de documentos, notas, decisiones y resúmenes.

Reglas:
- Cada recuerdo conserva su fuente (tipo, id, versión, sección/página), fecha, área, confianza, estado de
  validación y vigencia.
- Lo extraído automáticamente es 'inferred'; solo las decisiones aprobadas son 'confirmed'.
- Duplicados exactos se reutilizan; casi duplicados y contradicciones se registran como relaciones para
  revisión humana (no se resuelven arbitrariamente).
- Nuevas versiones de una fuente marcan como obsoletos los recuerdos de la versión anterior.
- Las fuentes eliminadas archivan sus recuerdos (política de retención: archivar, no borrar).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from wadima.activity import log_activity
from wadima.memory.extract import Candidate, extract_candidates
from wadima.models import (
    Area,
    Decision,
    Document,
    DocumentSummary,
    DocumentVersion,
    MemoryHistory,
    MemoryItem,
    MemoryRelation,
    Note,
    Project,
)
from wadima.realtime import emit
from wadima.security.auth import now_utc
from wadima.services import jobs

log = logging.getLogger("wadima.memory")

AGENT = "memory_agent"
NEAR_DUP_THRESHOLD = 0.85


def item_snapshot(item: MemoryItem) -> dict[str, Any]:
    return {
        "layer": item.layer,
        "kind": item.kind,
        "title": item.title,
        "content": item.content,
        "subject_key": item.subject_key,
        "value_text": item.value_text,
        "confidence": item.confidence,
        "validation": item.validation,
        "status": item.status,
        "valid_until": item.valid_until.isoformat() if item.valid_until else None,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "source_version": item.source_version,
        "source_ref": item.source_ref,
    }


def record_history(
    db: Session,
    item: MemoryItem,
    change: str,
    before: dict | None,
    *,
    actor_id: uuid.UUID | None = None,
    reason: str = "",
) -> None:
    db.add(
        MemoryHistory(
            memory_id=item.id,
            version=item.version,
            change=change,
            before=before,
            after=item_snapshot(item) if change != "delete" else None,
            actor_id=actor_id,
            actor_agent=None if actor_id else AGENT,
            reason=reason[:2000],
        )
    )


def content_hash(area_id: uuid.UUID, kind: str, content: str) -> str:
    return hashlib.sha256(f"{area_id}|{kind}|{content.strip().lower()}".encode()).hexdigest()


def upsert_item(
    db: Session,
    *,
    layer: str,
    kind: str,
    title: str,
    content: str,
    area_id: uuid.UUID,
    project_id: uuid.UUID | None,
    source_type: str,
    source_id: str,
    source_version: int | None = None,
    source_ref: dict | None = None,
    confidence: float = 0.5,
    validation: str = "inferred",
    subject_key: str | None = None,
    value_text: str | None = None,
) -> tuple[MemoryItem, str]:
    """Crea o reutiliza un recuerdo. Devuelve (item, acción) con acción en created|exists|duplicate."""
    h = content_hash(area_id, kind, content)
    same_source = (
        db.execute(
            select(MemoryItem).where(
                MemoryItem.content_hash == h,
                MemoryItem.source_type == source_type,
                MemoryItem.source_id == source_id,
                MemoryItem.status != "deleted",
            )
        )
        .scalars()
        .first()
    )
    if same_source is not None:
        if same_source.status == "obsolete":
            before = item_snapshot(same_source)
            same_source.status = "active"
            same_source.valid_until = None
            same_source.version += 1
            record_history(db, same_source, "update", before, reason="Vuelve a estar presente en la fuente")
        same_source.source_version = source_version
        same_source.source_ref = source_ref or {}
        return same_source, "exists"
    item = MemoryItem(
        layer=layer,
        kind=kind,
        title=title[:300],
        content=content,
        area_id=area_id,
        project_id=project_id,
        source_type=source_type,
        source_id=source_id,
        source_version=source_version,
        source_ref=source_ref or {},
        confidence=confidence,
        validation=validation,
        status="active",
        content_hash=h,
        subject_key=subject_key,
        value_text=value_text,
        valid_from=now_utc(),
    )
    db.add(item)
    db.flush()
    record_history(db, item, "create", None)
    action = "created"
    # Duplicado exacto procedente de otra fuente
    dup = db.execute(
        select(MemoryItem)
        .where(MemoryItem.content_hash == h, MemoryItem.id != item.id, MemoryItem.status == "active")
        .limit(1)
    ).scalar_one_or_none()
    if dup is not None:
        db.add(
            MemoryRelation(
                from_id=item.id, to_id=dup.id, relation="duplicates", score=1.0, detail="Mismo contenido en otra fuente"
            )
        )
        action = "duplicate"
    elif len(content) >= 40:
        near = db.execute(
            text(
                "SELECT id, similarity(content, :c) AS s FROM memory_items "
                "WHERE area_id = :a AND id <> :i AND status = 'active' AND kind = :k AND content % :c "
                "ORDER BY s DESC LIMIT 1"
            ),
            {"c": content, "a": area_id, "i": item.id, "k": kind},
        ).first()
        if near is not None and near.s >= NEAR_DUP_THRESHOLD:
            db.add(
                MemoryRelation(
                    from_id=item.id,
                    to_id=near.id,
                    relation="duplicates",
                    score=float(near.s),
                    detail=f"Contenido casi idéntico (similitud {near.s:.2f})",
                )
            )
            action = "duplicate"
    if subject_key and value_text:
        _detect_contradictions(db, item)
    return item, action


def _detect_contradictions(db: Session, item: MemoryItem) -> None:
    others = (
        db.execute(
            select(MemoryItem).where(
                MemoryItem.area_id == item.area_id,
                MemoryItem.subject_key == item.subject_key,
                MemoryItem.id != item.id,
                MemoryItem.status == "active",
                MemoryItem.value_text.is_not(None),
                (MemoryItem.project_id == item.project_id) if item.project_id else MemoryItem.project_id.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for other in others:
        if other.value_text == item.value_text:
            db.add(
                MemoryRelation(
                    from_id=item.id, to_id=other.id, relation="supports", score=1.0, detail="Mismo dato en otra fuente"
                )
            )
            continue
        if other.source_type == item.source_type and other.source_id == item.source_id:
            continue
        exists = db.execute(
            select(MemoryRelation.id).where(
                MemoryRelation.relation == "contradicts",
                ((MemoryRelation.from_id == item.id) & (MemoryRelation.to_id == other.id))
                | ((MemoryRelation.from_id == other.id) & (MemoryRelation.to_id == item.id)),
            )
        ).first()
        if not exists:
            db.add(
                MemoryRelation(
                    from_id=item.id,
                    to_id=other.id,
                    relation="contradicts",
                    score=1.0,
                    detail=f"«{item.title}» frente a «{other.title}»",
                )
            )
            emit(db, "memory.changed", area_id=item.area_id, contradiction=True)


def _obsolete_previous(db: Session, source_type: str, source_id: str, keep_version: int | None, reason: str) -> int:
    """Marca como obsoletos los recuerdos de versiones anteriores de la misma fuente."""
    rows = (
        db.execute(
            select(MemoryItem).where(
                MemoryItem.source_type == source_type,
                MemoryItem.source_id == source_id,
                MemoryItem.status == "active",
                MemoryItem.source_version != keep_version,
            )
        )
        .scalars()
        .all()
    )
    for item in rows:
        before = item_snapshot(item)
        item.status = "obsolete"
        item.valid_until = now_utc()
        item.version += 1
        record_history(db, item, "obsolete", before, reason=reason)
    return len(rows)


def _apply_candidates(
    db: Session,
    candidates: list[Candidate],
    *,
    area_id: uuid.UUID,
    project_id: uuid.UUID | None,
    source_type: str,
    source_id: str,
    source_version: int | None,
    base_ref: dict,
    layer_for: dict[str, str],
) -> dict[str, int]:
    stats = {"created": 0, "exists": 0, "duplicate": 0}
    for cand in candidates:
        subject = f"{project_id}|{cand.subject_key}" if cand.subject_key and project_id else cand.subject_key
        _, action = upsert_item(
            db,
            layer=layer_for.get(cand.kind, "semantic"),
            kind=cand.kind,
            title=cand.title,
            content=cand.content,
            area_id=area_id,
            project_id=project_id,
            source_type=source_type,
            source_id=source_id,
            source_version=source_version,
            source_ref={**base_ref, **cand.ref},
            confidence=cand.confidence,
            validation="inferred",
            subject_key=subject,
            value_text=cand.value_text,
        )
        stats[action] += 1
    return stats


LAYER_FOR = {"fact": "semantic", "concept": "semantic", "procedure": "operational", "agreement": "operational"}


# --------------------------------------------------------------------------- ingesta
@jobs.register("memory.ingest", AGENT)
def ingest(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    source_type = payload["source_type"]
    source_id = payload["source_id"]
    handler = {
        "document": _ingest_document,
        "note": _ingest_note,
        "decision": _ingest_decision,
        "project_summary": _ingest_project_summary,
        "ai_run": _ingest_ai_run,
        "task": _ingest_task,
    }.get(source_type)
    if handler is None:
        return {"skipped": f"tipo de fuente desconocido {source_type}"}
    result = handler(db, source_id)
    if result.get("area_id"):
        jobs.enqueue(
            db,
            "memory.consolidate",
            {"area_id": result["area_id"]},
            dedupe_key=f"memory:consolidate:{result['area_id']}",
            delay_seconds=30,
        )
        emit(db, "memory.changed", area_id=uuid.UUID(result["area_id"]))
    return result


def _ingest_document(db: Session, version_id: str) -> dict[str, Any]:
    version = db.get(DocumentVersion, uuid.UUID(version_id))
    if version is None or version.markdown is None:
        return {"skipped": "versión sin Markdown"}
    doc = db.get(Document, version.document_id)
    if doc is None or doc.deleted_at is not None:
        return {"skipped": "documento eliminado"}
    if doc.current_version != version.version:
        return {"skipped": "no es la versión vigente"}
    summaries = {
        s.level: s.content
        for s in db.execute(select(DocumentSummary).where(DocumentSummary.version_id == version.id)).scalars()
    }
    base_ref = {"document_id": str(doc.id), "version_id": str(version.id), "title": doc.title}
    # Capa documental: referencia al documento con su resumen.
    upsert_item(
        db,
        layer="documental",
        kind="document",
        title=doc.title,
        content=f"{doc.title} ({doc.ext.upper()}, v{version.version}). {summaries.get('short', '')}".strip(),
        area_id=doc.area_id,
        project_id=doc.project_id,
        source_type="document",
        source_id=str(doc.id),
        source_version=version.version,
        source_ref={**base_ref, "keywords": summaries.get("keywords", "")},
        confidence=0.9,
        validation="unverified",
    )
    candidates = extract_candidates(version.markdown)
    stats = _apply_candidates(
        db,
        candidates,
        area_id=doc.area_id,
        project_id=doc.project_id,
        source_type="document",
        source_id=str(doc.id),
        source_version=version.version,
        base_ref=base_ref,
        layer_for=LAYER_FOR,
    )
    db.flush()
    obsoleted = _obsolete_previous(
        db, "document", str(doc.id), version.version, f"Ya no figura en la versión {version.version} del documento"
    )
    if version.suspicious:
        log.warning("Documento %s con posibles instrucciones incrustadas: tratado como dato", doc.id)
    log_activity(
        db,
        "agent.result",
        f"Memory Agent procesó «{doc.title}»: {stats['created']} recuerdos nuevos, "
        f"{stats['duplicate']} duplicados, {obsoleted} obsoletos",
        actor_kind="agent",
        actor_name="Memory Agent",
        area_id=doc.area_id,
        project_id=doc.project_id,
        ref_type="document",
        ref_id=doc.id,
    )
    return {"area_id": str(doc.area_id), **stats, "obsoleted": obsoleted}


def _ingest_note(db: Session, note_id: str) -> dict[str, Any]:
    note = db.get(Note, uuid.UUID(note_id))
    if note is None or note.deleted_at is not None:
        return {"skipped": "nota eliminada"}
    base_ref = {"note_id": str(note.id), "title": note.title, "kind": note.kind}
    upsert_item(
        db,
        layer="documental",
        kind="note",
        title=note.title,
        content=f"{note.title}. {note.content[:400]}",
        area_id=note.area_id,
        project_id=note.project_id,
        source_type="note",
        source_id=str(note.id),
        source_version=note.version,
        source_ref=base_ref,
        confidence=0.9,
        validation="unverified",
    )
    layer_for = dict(LAYER_FOR)
    candidates = extract_candidates(note.content)
    if note.kind == "meeting":
        for c in candidates:
            if c.kind == "agreement":
                c.confidence = 0.65  # acuerdos anotados en un acta
    stats = _apply_candidates(
        db,
        candidates,
        area_id=note.area_id,
        project_id=note.project_id,
        source_type="note",
        source_id=str(note.id),
        source_version=note.version,
        base_ref=base_ref,
        layer_for=layer_for,
    )
    db.flush()
    obsoleted = _obsolete_previous(
        db, "note", str(note.id), note.version, f"Ya no figura en la versión {note.version} de la nota"
    )
    return {"area_id": str(note.area_id), **stats, "obsoleted": obsoleted}


def _ingest_decision(db: Session, decision_id: str) -> dict[str, Any]:
    d = db.get(Decision, uuid.UUID(decision_id))
    if d is None:
        return {"skipped": "decisión inexistente"}
    existing = (
        db.execute(
            select(MemoryItem).where(
                MemoryItem.source_type == "decision", MemoryItem.source_id == str(d.id), MemoryItem.status != "deleted"
            )
        )
        .scalars()
        .all()
    )
    if d.status == "approved":
        content = f"Decisión aprobada: {d.title}." + (f" {d.body}" if d.body else "")
        item, _ = upsert_item(
            db,
            layer="operational",
            kind="decision",
            title=d.title,
            content=content,
            area_id=d.area_id,
            project_id=d.project_id,
            source_type="decision",
            source_id=str(d.id),
            source_version=1,
            source_ref={"decision_id": str(d.id), "decided_at": d.decided_at.isoformat() if d.decided_at else None},
            confidence=1.0,
            validation="confirmed",
        )
        return {"area_id": str(d.area_id), "item": str(item.id)}
    for item in existing:  # rechazada o sustituida: deja de estar vigente
        if item.status == "active":
            before = item_snapshot(item)
            item.status = "obsolete"
            item.valid_until = now_utc()
            item.version += 1
            record_history(db, item, "obsolete", before, reason=f"La decisión pasó a estado {d.status}")
    return {"area_id": str(d.area_id), "obsoleted": len(existing)}


def _ingest_project_summary(db: Session, project_id: str) -> dict[str, Any]:
    p = db.get(Project, uuid.UUID(project_id))
    if p is None or not p.last_summary:
        return {"skipped": "sin resumen"}
    version = int(p.last_summary_at.timestamp()) if p.last_summary_at else None
    _obsolete_previous(db, "project_summary", str(p.id), version, "Nuevo resumen de actividad")
    upsert_item(
        db,
        layer="consolidated",
        kind="summary",
        title=f"Resumen de actividad · {p.name}",
        content=p.last_summary,
        area_id=p.area_id,
        project_id=p.id,
        source_type="project_summary",
        source_id=str(p.id),
        source_version=version,
        source_ref={"project_id": str(p.id)},
        confidence=0.95,
        validation="unverified",
    )
    return {"area_id": str(p.area_id)}


def _ingest_task(db: Session, task_id: str) -> dict[str, Any]:
    """Resultados de tareas terminadas: memoria operativa (hecho registrado por una persona)."""
    from wadima.models import Task

    t = db.get(Task, uuid.UUID(task_id))
    if t is None:
        return {"skipped": "tarea inexistente"}
    p = db.get(Project, t.project_id)
    if t.status != "done":
        _obsolete_previous(db, "task", str(t.id), -1, "La tarea se reabrió")
        return {"area_id": str(p.area_id)}
    done_at = t.completed_at.isoformat() if t.completed_at else None
    content = f"Tarea completada en «{p.name}»: {t.title}." + (f" {t.description[:2000]}" if t.description else "")
    upsert_item(
        db,
        layer="operational",
        kind="task_result",
        title=f"Tarea completada: {t.title}",
        content=content,
        area_id=p.area_id,
        project_id=p.id,
        source_type="task",
        source_id=str(t.id),
        source_version=t.version,
        source_ref={"project_id": str(p.id), "task_id": str(t.id), "completed_at": done_at, "title": t.title},
        confidence=0.9,
        validation="unverified",
    )
    return {"area_id": str(p.area_id)}


def _ingest_ai_run(db: Session, run_id: str) -> dict[str, Any]:
    """Un resultado de IA compartido es una inferencia: entra como recuerdo operativo de baja confianza."""
    from wadima.models import AIRun

    r = db.get(AIRun, uuid.UUID(run_id))
    if r is None or not r.shared or r.area_id is None or not r.output:
        return {"skipped": "ejecución no compartida"}
    upsert_item(
        db,
        layer="operational",
        kind="agent_result",
        title=f"Resultado de IA: {r.prompt[:200]}",
        content=r.output[:20000],
        area_id=r.area_id,
        project_id=r.project_id,
        source_type="ai_run",
        source_id=str(r.id),
        source_version=1,
        source_ref={"run_id": str(r.id), "model": r.model, "title": r.prompt[:120]},
        confidence=0.4,
        validation="inferred",
    )
    return {"area_id": str(r.area_id)}


# --------------------------------------------------------------------------- consolidación por área
@jobs.register("memory.consolidate", AGENT)
def consolidate(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    area = db.get(Area, uuid.UUID(payload["area_id"]))
    if area is None:
        return {"skipped": "área inexistente"}
    md = consolidated_markdown(db, area)
    version = int(now_utc().timestamp())
    _obsolete_previous(db, "area_consolidation", str(area.id), version, "Nueva consolidación del área")
    upsert_item(
        db,
        layer="consolidated",
        kind="summary",
        title=f"Memoria consolidada · {area.name}",
        content=md,
        area_id=area.id,
        project_id=None,
        source_type="area_consolidation",
        source_id=str(area.id),
        source_version=version,
        source_ref={"area_id": str(area.id)},
        confidence=0.9,
        validation="unverified",
    )
    from wadima.drive.sync import track

    track(
        db,
        "memory_export",
        area.id,
        area_id=area.id,
        folder_key=f"Memory/{area.slug}",
        name=f"memoria-consolidada-{area.slug}.md",
        mime="text/markdown",
        content=md.encode("utf-8"),
        delay_seconds=30,
    )
    return {"area_id": str(area.id), "chars": len(md)}


def consolidated_markdown(db: Session, area: Area) -> str:
    active = select(MemoryItem).where(MemoryItem.area_id == area.id, MemoryItem.status == "active")
    decisions = (
        db.execute(active.where(MemoryItem.kind == "decision").order_by(MemoryItem.created_at.desc()).limit(50))
        .scalars()
        .all()
    )
    procedures = db.execute(active.where(MemoryItem.kind == "procedure").limit(30)).scalars().all()
    agreements = db.execute(active.where(MemoryItem.kind == "agreement").limit(40)).scalars().all()
    facts = (
        db.execute(active.where(MemoryItem.kind == "fact").order_by(MemoryItem.confidence.desc()).limit(80))
        .scalars()
        .all()
    )
    docs = (
        db.execute(active.where(MemoryItem.layer == "documental").order_by(MemoryItem.updated_at.desc()).limit(60))
        .scalars()
        .all()
    )
    contradictions = db.execute(
        select(func.count())
        .select_from(MemoryRelation)
        .join(MemoryItem, MemoryItem.id == MemoryRelation.from_id)
        .where(
            MemoryRelation.relation == "contradicts", MemoryRelation.resolved.is_(False), MemoryItem.area_id == area.id
        )
    ).scalar_one()
    lines = [
        f"# Memoria consolidada · {area.name}",
        "",
        f"_Generada por el Memory Agent el {now_utc():%Y-%m-%d %H:%M} UTC a partir de fuentes registradas. "
        "Los elementos «inferidos» no están confirmados._",
        "",
    ]
    if contradictions:
        lines += [f"> ⚠ Hay {contradictions} contradicción(es) pendientes de revisión en esta área.", ""]
    if decisions:
        lines.append("## Decisiones aprobadas (confirmadas)")
        lines += [f"- {d.title}" for d in decisions]
        lines.append("")
    if procedures:
        lines.append("## Procedimientos (inferidos)")
        for p in procedures:
            lines += [f"### {p.title}", p.content, ""]
    if agreements:
        lines.append("## Acuerdos (inferidos de actas y notas)")
        lines += [f"- {a.content}" for a in agreements]
        lines.append("")
    if facts:
        lines.append("## Datos clave (inferidos)")
        lines += [f"- {f.content} _(fuente: {f.source_ref.get('title', f.source_type)})_" for f in facts]
        lines.append("")
    if docs:
        lines.append("## Fuentes documentales")
        lines += [f"- {d.title}" for d in docs]
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------------------- fuentes eliminadas
@jobs.register("memory.source_deleted", AGENT)
def source_deleted(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    source_id = payload.get("document_id") or payload.get("source_id")
    source_type = payload.get("source_type", "document")
    rows = (
        db.execute(
            select(MemoryItem).where(
                MemoryItem.source_type == source_type,
                MemoryItem.source_id == str(source_id),
                MemoryItem.status.in_(("active", "obsolete", "contradicted")),
            )
        )
        .scalars()
        .all()
    )
    areas = set()
    for item in rows:
        before = item_snapshot(item)
        item.status = "archived"
        item.valid_until = item.valid_until or now_utc()
        item.version += 1
        record_history(db, item, "archive", before, reason="La fuente fue eliminada (retención: archivar)")
        areas.add(item.area_id)
    for area_id in areas:
        jobs.enqueue(
            db,
            "memory.consolidate",
            {"area_id": str(area_id)},
            dedupe_key=f"memory:consolidate:{area_id}",
            delay_seconds=30,
        )
        emit(db, "memory.changed", area_id=area_id)
    return {"archived": len(rows)}


# --------------------------------------------------------------------------- Drive
def _register_drive() -> None:
    from wadima.drive.sync import provider

    @provider("memory_export")
    def memory_export(db: Session, row) -> tuple[bytes, str]:
        area = db.get(Area, uuid.UUID(row.local_id))
        if area is None:
            raise LookupError("el área no existe")
        return consolidated_markdown(db, area).encode("utf-8"), "text/markdown"


_register_drive()
