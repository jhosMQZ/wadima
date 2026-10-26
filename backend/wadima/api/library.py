"""Biblioteca documental: subida, versiones, visores (original, Markdown, diferencias, tablas) y búsqueda."""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from wadima.activity import log_activity
from wadima.api.serial import iso
from wadima.config import get_settings
from wadima.db import get_db
from wadima.drive.sync import area_slug, track
from wadima.library import pipeline as _pipeline  # noqa: F401 - registra el trabajo document.process
from wadima.library.storage import read_blob, store_blob
from wadima.library.validate import ValidationError, validate
from wadima.models import Document, DocumentChunk, DocumentSummary, DocumentVersion, DriveFile, Project, User
from wadima.realtime import emit
from wadima.security.auth import Principal, now_utc, require_password_ok
from wadima.security.perms import accessible_area_ids, require_area
from wadima.services import jobs

router = APIRouter(prefix="/api/library", tags=["library"])

INLINE_TYPES = {"application/pdf", "text/plain", "text/csv", "text/markdown", "application/json"}


def doc_out(d: Document, extra: dict | None = None) -> dict:
    return {
        "id": str(d.id),
        "area_id": str(d.area_id),
        "project_id": str(d.project_id) if d.project_id else None,
        "title": d.title,
        "filename": d.filename,
        "ext": d.ext,
        "mime": d.mime,
        "category": d.category,
        "tags": d.tags or [],
        "status": d.status,
        "error": d.error,
        "current_version": d.current_version,
        "uploaded_by": str(d.uploaded_by) if d.uploaded_by else None,
        "created_at": iso(d.created_at),
        "updated_at": iso(d.updated_at),
        **(extra or {}),
    }


def version_out(v: DocumentVersion, author: str | None = None) -> dict:
    return {
        "id": str(v.id),
        "version": v.version,
        "sha256": v.sha256,
        "size": v.size,
        "status": v.status,
        "error": v.error,
        "report": v.report,
        "suspicious": v.suspicious,
        "created_at": iso(v.created_at),
        "processed_at": iso(v.processed_at),
        "author": author,
    }


def _doc(db: Session, principal: Principal, doc_id: uuid.UUID, min_role: str = "viewer") -> Document:
    d = db.get(Document, doc_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(404, "Documento no encontrado")
    require_area(db, principal, d.area_id, min_role)
    return d


def _version(db: Session, doc: Document, version: int) -> DocumentVersion:
    v = db.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id, DocumentVersion.version == version)
    ).scalar_one_or_none()
    if v is None:
        raise HTTPException(404, "Versión no encontrada")
    return v


def _clean_tags(raw: str | list[str] | None) -> list[str]:
    items = raw.split(",") if isinstance(raw, str) else (raw or [])
    return sorted({t.strip().lower()[:40] for t in items if t.strip()})[:20]


@router.post("/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    area_id: uuid.UUID = Form(...),
    project_id: uuid.UUID | None = Form(None),
    title: str = Form(""),
    tags: str = Form(""),
    category: str = Form("general"),
    document_id: uuid.UUID | None = Form(None),
    allow_duplicate: bool = Form(False),
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, f"El archivo supera el límite de {get_settings().max_upload_mb} MB")
    require_area(db, principal, area_id, "editor")
    try:
        info = validate(file.filename or "documento", content)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    if project_id:
        project = db.get(Project, project_id)
        if project is None or project.area_id != area_id:
            raise HTTPException(422, "El proyecto no pertenece al área")
    sha, rel = store_blob(content)
    if document_id is None and not allow_duplicate:
        dup = db.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.area_id == area_id, Document.deleted_at.is_(None), DocumentVersion.sha256 == sha)
            .limit(1)
        ).first()
        if dup:
            raise HTTPException(
                409,
                {
                    "message": "Este archivo ya existe en la biblioteca del área",
                    "duplicate": doc_out(dup[0]),
                    "version": dup[1].version,
                },
            )
    if document_id:
        doc = _doc(db, principal, document_id, "editor")
        if doc.area_id != area_id:
            raise HTTPException(422, "El documento pertenece a otra área")
        last = db.execute(
            select(DocumentVersion.sha256)
            .where(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        if last == sha:
            raise HTTPException(409, {"message": "El contenido es idéntico a la versión actual"})
        doc.current_version += 1
        doc.status = "received"
        doc.error = ""
        kind, summary = "document.version", f"Subió la versión {doc.current_version} de «{doc.title}»"
    else:
        doc = Document(
            area_id=area_id,
            project_id=project_id,
            title=(title.strip() or info.filename)[:250],
            filename=info.filename,
            ext=info.ext,
            mime=info.mime,
            category=category.strip()[:60] or "general",
            tags=_clean_tags(tags),
            status="received",
            uploaded_by=principal.id,
        )
        db.add(doc)
        db.flush()
        kind, summary = "document.added", f"Añadió «{doc.title}» a la biblioteca"
    version = DocumentVersion(
        document_id=doc.id,
        version=doc.current_version,
        sha256=sha,
        size=len(content),
        blob_path=rel,
        status="validated",
        created_by=principal.id,
    )
    db.add(version)
    db.flush()
    track(
        db,
        "document_original",
        version.id,
        area_id=area_id,
        folder_key=f"Library/{area_slug(db, area_id)}",
        name=f"{doc.title}.v{version.version}.{info.ext}"
        if not doc.title.lower().endswith(f".{info.ext}")
        else f"{doc.title.rsplit('.', 1)[0]}.v{version.version}.{info.ext}",
        mime=info.mime,
        content=content,
    )
    jobs.enqueue(db, "document.process", {"version_id": str(version.id)}, dedupe_key=f"doc:{version.id}")
    log_activity(
        db,
        kind,
        summary,
        user=principal.user,
        area_id=area_id,
        project_id=doc.project_id,
        ref_type="document",
        ref_id=doc.id,
        data={"sha256": sha, "size": len(content)},
    )
    emit(db, "document.changed", area_id=area_id, id=str(doc.id), status="received")
    db.commit()
    return {"document": doc_out(doc), "version": version_out(version)}


@router.get("/documents")
def list_documents(
    area_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    tag: str | None = None,
    category: str | None = None,
    status: str | None = None,
    q: str | None = None,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    ids = accessible_area_ids(db, principal)
    if not ids:
        return []
    query = select(Document).where(Document.area_id.in_(ids), Document.deleted_at.is_(None))
    if area_id:
        query = query.where(Document.area_id == area_id)
    if project_id:
        query = query.where(Document.project_id == project_id)
    if tag:
        query = query.where(Document.tags.any(tag.lower()))
    if category:
        query = query.where(Document.category == category)
    if status:
        query = query.where(Document.status == status)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(func.lower(Document.title).like(like), func.lower(Document.filename).like(like)))
    docs = db.execute(query.order_by(Document.updated_at.desc()).limit(500)).scalars().all()
    states: dict[uuid.UUID, str] = {}
    if docs:
        current = dict(
            db.execute(
                select(DocumentVersion.id, DocumentVersion.document_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(Document.id.in_([d.id for d in docs]), DocumentVersion.version == Document.current_version)
            ).all()
        )
        for local_id, state in db.execute(
            select(DriveFile.local_id, DriveFile.sync_state).where(
                DriveFile.local_kind == "document_original", DriveFile.local_id.in_([str(v) for v in current])
            )
        ).all():
            states[current[uuid.UUID(local_id)]] = state
    return [doc_out(d, {"drive_state": states.get(d.id)}) for d in docs]


@router.get("/documents/{doc_id}")
def get_document(doc_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    d = _doc(db, principal, doc_id)
    versions = db.execute(
        select(DocumentVersion, User.display_name)
        .outerjoin(User, User.id == DocumentVersion.created_by)
        .where(DocumentVersion.document_id == d.id)
        .order_by(DocumentVersion.version.desc())
    ).all()
    current = next((v for v, _ in versions if v.version == d.current_version), None)
    summaries = {}
    if current:
        summaries = {
            s.level: s.content
            for s in db.execute(select(DocumentSummary).where(DocumentSummary.version_id == current.id)).scalars()
        }
    drive = {}
    for kind, local in (("document_original", current), ("document_markdown", current)):
        if local is None:
            continue
        row = db.execute(
            select(DriveFile).where(DriveFile.local_kind == kind, DriveFile.local_id == str(local.id))
        ).scalar_one_or_none()
        if row:
            drive[kind] = {
                "state": row.sync_state,
                "drive_file_id": row.drive_file_id,
                "error": row.last_error,
                "id": str(row.id),
            }
    uploader = db.get(User, d.uploaded_by) if d.uploaded_by else None
    return doc_out(
        d,
        {
            "versions": [version_out(v, author) for v, author in versions],
            "summaries": summaries,
            "drive": drive,
            "uploaded_by_name": uploader.display_name if uploader else None,
        },
    )


class DocPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=250)
    tags: list[str] | None = None
    category: str | None = Field(default=None, max_length=60)
    project_id: uuid.UUID | None = None
    clear_project: bool = False


@router.patch("/documents/{doc_id}")
def update_document(
    doc_id: uuid.UUID,
    body: DocPatch,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    d = _doc(db, principal, doc_id, "editor")
    if body.title is not None:
        d.title = body.title.strip()
    if body.tags is not None:
        d.tags = _clean_tags(body.tags)
    if body.category is not None:
        d.category = body.category.strip() or "general"
    if body.clear_project:
        d.project_id = None
    elif body.project_id is not None:
        p = db.get(Project, body.project_id)
        if p is None or p.area_id != d.area_id:
            raise HTTPException(422, "El proyecto no pertenece al área")
        d.project_id = p.id
    emit(db, "document.changed", area_id=d.area_id, id=str(d.id), status=d.status)
    db.commit()
    return doc_out(d)


@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    """Borrado lógico: el original se conserva (local y Drive) y el Memory Agent revisa sus dependientes."""
    d = _doc(db, principal, doc_id, "editor")
    d.deleted_at = now_utc()
    log_activity(
        db,
        "document.deleted",
        f"Eliminó «{d.title}» de la biblioteca",
        user=principal.user,
        area_id=d.area_id,
        project_id=d.project_id,
        ref_type="document",
        ref_id=d.id,
    )
    jobs.enqueue(
        db,
        "memory.source_deleted",
        {"source_type": "document", "document_id": str(d.id)},
        dedupe_key=f"memory:deleted:{d.id}",
    )
    emit(db, "document.changed", area_id=d.area_id, id=str(d.id), status="deleted")
    db.commit()
    return {"ok": True}


@router.post("/documents/{doc_id}/reprocess")
def reprocess(doc_id: uuid.UUID, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    d = _doc(db, principal, doc_id, "editor")
    v = _version(db, d, d.current_version)
    v.status = "validated"
    d.status = "validated"
    jobs.enqueue(db, "document.process", {"version_id": str(v.id)}, dedupe_key=f"doc:{v.id}")
    emit(db, "document.changed", area_id=d.area_id, id=str(d.id), status="validated")
    db.commit()
    return {"ok": True}


@router.get("/documents/{doc_id}/versions/{version}/original")
def download_original(
    doc_id: uuid.UUID,
    version: int,
    inline: bool = False,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    d = _doc(db, principal, doc_id)
    v = _version(db, d, version)
    data = read_blob(v.blob_path)
    disposition = "inline" if inline and d.mime in INLINE_TYPES else "attachment"
    mime = d.mime if disposition == "inline" else "application/octet-stream"
    name = quote(d.filename)
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{name}",
            # El original es contenido no confiable: sin scripts ni acceso al origen de WADIMA.
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/documents/{doc_id}/versions/{version}/content")
def version_content(
    doc_id: uuid.UUID, version: int, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    d = _doc(db, principal, doc_id)
    v = _version(db, d, version)
    return {
        "markdown": v.markdown,
        "plain_text": v.plain_text,
        "tables": v.tables,
        "report": v.report,
        "status": v.status,
        "error": v.error,
        "suspicious": v.suspicious,
    }


@router.get("/documents/{doc_id}/versions/{version}/chunks")
def version_chunks(
    doc_id: uuid.UUID, version: int, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    d = _doc(db, principal, doc_id)
    v = _version(db, d, version)
    rows = db.execute(select(DocumentChunk).where(DocumentChunk.version_id == v.id).order_by(DocumentChunk.ord))
    return [
        {
            "id": str(c.id),
            "ord": c.ord,
            "heading": c.heading,
            "locator": c.locator,
            "content": c.content,
            "token_estimate": c.token_estimate,
            "suspicious": c.suspicious,
        }
        for c in rows.scalars()
    ]


@router.get("/search")
def search(
    q: str,
    area_id: uuid.UUID | None = None,
    limit: int = 20,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    """Búsqueda de texto completo (PostgreSQL) sobre los fragmentos de la versión vigente de cada documento."""
    ids = accessible_area_ids(db, principal)
    if area_id:
        if area_id not in ids:
            raise HTTPException(404, "Área no encontrada")
        ids = [area_id]
    if not ids or not q.strip():
        return []
    sql = text(
        """
        WITH query AS (SELECT websearch_to_tsquery('simple', wadima_unaccent(:q)) AS tq)
        SELECT c.id, c.document_id, c.heading, c.locator, d.title, d.ext, d.area_id, v.version,
               ts_rank_cd(c.tsv, query.tq) AS rank,
               ts_headline('simple', c.content, query.tq,
                           'StartSel=<<, StopSel=>>, MaxWords=35, MinWords=12, MaxFragments=2') AS snippet
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        JOIN document_versions v ON v.id = c.version_id AND v.version = d.current_version
        CROSS JOIN query
        WHERE c.tsv @@ query.tq AND d.deleted_at IS NULL AND d.area_id = ANY(:areas)
        ORDER BY rank DESC
        LIMIT :limit
        """
    )
    rows = db.execute(sql, {"q": q, "areas": ids, "limit": min(limit, 100)}).mappings().all()
    return [
        {
            "chunk_id": str(r["id"]),
            "document_id": str(r["document_id"]),
            "title": r["title"],
            "ext": r["ext"],
            "heading": r["heading"],
            "locator": r["locator"],
            "version": r["version"],
            "rank": float(r["rank"]),
            "snippet": r["snippet"],
        }
        for r in rows
    ]
