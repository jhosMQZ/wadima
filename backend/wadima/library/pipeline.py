"""Pipeline asíncrono de documentos: validar → guardar original (local + Drive) → extraer → convertir →
validar extracción → fragmentar → resumir → indexar → avisar al Memory Agent → registrar resultado."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from wadima.activity import log_activity
from wadima.drive.sync import area_slug, provider, remote_applier, track
from wadima.library.chunking import detect_injection, estimate_tokens, split_markdown, summarize
from wadima.library.extract import Extraction, ExtractionError, extract, tesseract_available
from wadima.library.storage import read_blob, store_blob
from wadima.library.validate import TYPES
from wadima.models import Document, DocumentChunk, DocumentSummary, DocumentVersion
from wadima.realtime import emit
from wadima.security.auth import now_utc
from wadima.services import jobs

log = logging.getLogger("wadima.library")


def assess(ex: Extraction) -> tuple[str, dict]:
    text = ex.plain_text or ""
    stripped = text.strip()
    n = len(stripped)
    bad = text.count("�")
    ratio = bad / max(n, 1)
    issues: list[str] = []
    if n == 0 and not ex.tables:
        issues.append("No se extrajo texto ni tablas")
    if ratio > 0.02:
        issues.append(f"{ratio:.1%} de caracteres no reconocidos (codificación o fuente incrustada)")
    if ex.needs_ocr:
        issues.append("Contiene páginas escaneadas sin OCR")
    status = "needs_review" if issues else "converted"
    report = {
        "chars_original": n,
        "chars_markdown": len(ex.markdown),
        "words": len(stripped.split()),
        "pages": ex.pages,
        "tables": len(ex.tables),
        "replacement_char_ratio": round(ratio, 4),
        "warnings": ex.warnings,
        "issues": issues,
        "noise_removed": ex.noise_removed,
        "needs_ocr": ex.needs_ocr,
        "ocr_available": tesseract_available(),
        "token_estimate_original": estimate_tokens(text),
        "token_estimate_markdown": estimate_tokens(ex.markdown),
        "token_estimate_method": "aproximación de 4 caracteres por token (no medida por el proveedor)",
        "meta": ex.meta,
    }
    return status, report


@jobs.register("document.process", "doc_pipeline")
def process_version(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    version = db.get(DocumentVersion, uuid.UUID(payload["version_id"]))
    if version is None:
        return {"skipped": "versión inexistente"}
    doc = db.get(Document, version.document_id)
    if doc is None or doc.deleted_at is not None:
        return {"skipped": "documento eliminado"}
    family = TYPES[doc.ext][0]
    _set_status(db, doc, version, "processing")
    db.commit()
    try:
        content = read_blob(version.blob_path)
        ex = extract(content, family, doc.ext)
    except ExtractionError as exc:
        return _fail(db, doc, version, str(exc))
    except Exception as exc:  # noqa: BLE001 - cualquier fallo del extractor se registra, no se oculta
        log.exception("Fallo inesperado extrayendo %s", doc.filename)
        return _fail(db, doc, version, f"Error inesperado al extraer ({type(exc).__name__}): {exc}")

    status, report = assess(ex)
    version.markdown = ex.markdown
    version.plain_text = ex.plain_text
    version.tables = ex.tables
    chunks = split_markdown(ex.markdown)
    injections = sorted({hit for ch in chunks for hit in detect_injection(ch.content)})
    version.suspicious = bool(injections)
    report["chunks"] = len(chunks)
    report["suspicious_instructions"] = injections[:20]
    version.report = report
    # Índice: se reemplazan los fragmentos de esta versión.
    db.execute(delete(DocumentChunk).where(DocumentChunk.version_id == version.id))
    db.execute(delete(DocumentSummary).where(DocumentSummary.version_id == version.id))
    for i, ch in enumerate(chunks):
        db.add(
            DocumentChunk(
                document_id=doc.id,
                version_id=version.id,
                area_id=doc.area_id,
                ord=i,
                heading=ch.heading,
                locator=ch.locator,
                content=ch.content,
                content_hash=ch.content_hash,
                token_estimate=estimate_tokens(ch.content),
                suspicious=bool(detect_injection(ch.content)),
            )
        )
    summary = summarize(ex.markdown)
    for level in ("short", "sections", "outline", "keywords"):
        if summary[level]:
            db.add(DocumentSummary(document_id=doc.id, version_id=version.id, level=level, content=summary[level]))
    version.processed_at = now_utc()
    final = "indexed" if status == "converted" else "needs_review"
    _set_status(db, doc, version, final)
    db.flush()
    track(
        db,
        "document_markdown",
        version.id,
        area_id=doc.area_id,
        folder_key=f"Markdown/{area_slug(db, doc.area_id)}",
        name=f"{doc.title}.v{version.version}.md",
        mime="text/markdown",
        content=ex.markdown.encode("utf-8"),
    )
    log_activity(
        db,
        "document.converted",
        f"«{doc.title}» convertido a Markdown ({len(chunks)} fragmentos"
        f"{', requiere revisión' if final == 'needs_review' else ''})",
        actor_kind="agent",
        actor_name="Conversor documental",
        area_id=doc.area_id,
        project_id=doc.project_id,
        ref_type="document",
        ref_id=doc.id,
    )
    jobs.enqueue(
        db,
        "memory.ingest",
        {"source_type": "document", "source_id": str(version.id)},
        dedupe_key=f"memory:document:{version.id}",
    )
    return {"status": final, "chunks": len(chunks), "issues": report["issues"]}


def _set_status(db: Session, doc: Document, version: DocumentVersion, status: str, error: str = "") -> None:
    version.status = status
    version.error = error
    if doc.current_version == version.version:
        doc.status = status
        doc.error = error
    emit(db, "document.changed", area_id=doc.area_id, id=str(doc.id), status=status)


def _fail(db: Session, doc: Document, version: DocumentVersion, message: str) -> dict[str, Any]:
    db.rollback()
    doc = db.get(Document, doc.id)
    version = db.get(DocumentVersion, version.id)
    version.report = {**(version.report or {}), "issues": [message]}
    _set_status(db, doc, version, "error", message)
    log_activity(
        db,
        "document.error",
        f"Error al procesar «{doc.title}»: {message[:200]}",
        actor_kind="agent",
        actor_name="Conversor documental",
        area_id=doc.area_id,
        project_id=doc.project_id,
        ref_type="document",
        ref_id=doc.id,
    )
    return {"status": "error", "error": message}


# --------------------------------------------------------------------------- Drive
@provider("document_original")
def original_content(db: Session, row) -> tuple[bytes, str]:
    version = db.get(DocumentVersion, uuid.UUID(row.local_id))
    if version is None:
        raise LookupError("la versión del documento no existe")
    doc = db.get(Document, version.document_id)
    return read_blob(version.blob_path), doc.mime if doc else "application/octet-stream"


@provider("document_markdown")
def markdown_content(db: Session, row) -> tuple[bytes, str]:
    version = db.get(DocumentVersion, uuid.UUID(row.local_id))
    if version is None or version.markdown is None:
        raise LookupError("no hay Markdown para esta versión")
    return version.markdown.encode("utf-8"), "text/markdown"


@remote_applier("document_original")
def original_from_drive(db: Session, row, data: bytes, meta: dict) -> None:
    """Un original modificado en Drive se importa como nueva versión (el anterior se conserva)."""
    old = db.get(DocumentVersion, uuid.UUID(row.local_id))
    doc = db.get(Document, old.document_id) if old else None
    if doc is None:
        raise LookupError("el documento no existe")
    sha, rel = store_blob(data)
    doc.current_version += 1
    new = DocumentVersion(
        document_id=doc.id, version=doc.current_version, sha256=sha, size=len(data), blob_path=rel, status="received"
    )
    db.add(new)
    db.flush()
    doc.status = "received"
    # La fila de Drive pasa a seguir a la nueva versión local (mismo archivo de Drive).
    row.local_id = str(new.id)
    jobs.enqueue(db, "document.process", {"version_id": str(new.id)}, dedupe_key=f"doc:{new.id}")
    log_activity(
        db,
        "document.version",
        f"Nueva versión de «{doc.title}» importada desde Google Drive",
        actor_kind="agent",
        actor_name="Sincronizador de Drive",
        area_id=doc.area_id,
        project_id=doc.project_id,
        ref_type="document",
        ref_id=doc.id,
    )


def latest_version(db: Session, doc: Document) -> DocumentVersion | None:
    return db.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == doc.id, DocumentVersion.version == doc.current_version
        )
    ).scalar_one_or_none()
