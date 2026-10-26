"""Control horario: entrada/salida, historial, totales, aprobación, incidencias y exportación."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima import org as orgsvc
from wadima import timeclock as tc
from wadima.db import get_db
from wadima.models import TimeEntry, TimeEvent, User
from wadima.security.auth import Principal, client_ip, now_utc, require_password_ok

router = APIRouter(prefix="/api/time", tags=["time"])


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=300)


class ClockOutIn(NoteIn):
    entry_id: uuid.UUID


class CloseIn(BaseModel):
    clock_out_at: datetime
    reason: str = Field(min_length=1, max_length=300)


class ReviewIn(BaseModel):
    note: str = Field(default="", max_length=300)


def _names(db: Session) -> dict[uuid.UUID, str]:
    return {u.id: u.display_name for u in db.execute(select(User)).scalars()}


def _err(exc: tc.TimeError, db: Session) -> JSONResponse:
    body: dict = {"detail": exc.message}
    if exc.entry is not None:
        org = orgsvc.get_org(db)
        body["entry"] = tc.entry_out(exc.entry, _names(db), orgsvc.zone(org), now_utc())
    return JSONResponse(body, status_code=exc.status)


def _period(org, start: date | None, end: date | None) -> tuple[date, date]:
    today = now_utc().astimezone(orgsvc.zone(org)).date()
    start = start or today.replace(day=1)
    end = end or today
    if end < start:
        raise HTTPException(422, "La fecha final es anterior a la inicial")
    if (end - start).days > 400:
        raise HTTPException(422, "El periodo máximo es de 400 días")
    return start, end


def _users_scope(db: Session, principal: Principal, org, user_id: uuid.UUID | None) -> list[uuid.UUID] | None:
    """None = toda la organización (si se permite ver a todos)."""
    if user_id is not None:
        if not tc.can_view_user(db, principal, org, user_id):
            raise HTTPException(404, "Persona no encontrada")
        return [user_id]
    if orgsvc.time_settings(org)["visibility"] == "all" or principal.id in tc.approvers(db, org):
        return None
    return [principal.id]


@router.get("/status")
def my_status(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    org = orgsvc.get_org(db)
    tz = orgsvc.zone(org)
    now = now_utc()
    entry = tc.open_entry(db, principal.id)
    today = now.astimezone(tz).date()
    summary = tc.summarize(tc.entries_between(db, tz, today, today, [principal.id]), tz, today, today, now)
    names = _names(db)
    last = db.execute(
        select(TimeEntry)
        .where(TimeEntry.user_id == principal.id, TimeEntry.clock_out_at.is_not(None))
        .order_by(TimeEntry.clock_out_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return {
        "server_time": now.isoformat(),
        "timezone": org.timezone,
        "open": tc.entry_out(entry, names, tz, now) if entry else None,
        "last_closed": tc.entry_out(last, names, tz, now) if last else None,
        "today": summary["totals"],
        "policy": orgsvc.time_settings(org),
        "can_approve": principal.id in tc.approvers(db, org),
    }


@router.post("/clock-in", status_code=201)
def do_clock_in(
    body: NoteIn, request: Request, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)
):
    org = orgsvc.get_org(db)
    entry, created = tc.clock_in(db, principal, org, ip=client_ip(request), note=body.note)
    db.commit()
    out = tc.entry_out(entry, _names(db), orgsvc.zone(org), now_utc())
    if not created:
        return JSONResponse(
            {"detail": "Ya tiene una entrada registrada sin salida; no se creó otra.", "entry": out}, status_code=409
        )
    return {"entry": out}


@router.post("/clock-out")
def do_clock_out(
    body: ClockOutIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    org = orgsvc.get_org(db)
    try:
        entry = tc.clock_out(db, principal, org, body.entry_id, ip=client_ip(request), note=body.note)
    except tc.TimeError as exc:
        db.rollback()
        return _err(exc, db)
    db.commit()
    return {"entry": tc.entry_out(entry, _names(db), orgsvc.zone(org), now_utc())}


@router.post("/entries/{entry_id}/declare-exit")
def do_declare_exit(
    entry_id: uuid.UUID,
    body: CloseIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    org = orgsvc.get_org(db)
    try:
        entry = tc.declare_exit(db, principal, org, entry_id, body.clock_out_at, body.reason, client_ip(request))
    except tc.TimeError as exc:
        db.rollback()
        return _err(exc, db)
    db.commit()
    return {"entry": tc.entry_out(entry, _names(db), orgsvc.zone(org), now_utc())}


@router.post("/entries/{entry_id}/close")
def do_admin_close(
    entry_id: uuid.UUID,
    body: CloseIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    org = orgsvc.get_org(db)
    try:
        entry = tc.admin_close(db, principal, org, entry_id, body.clock_out_at, body.reason, client_ip(request))
    except tc.TimeError as exc:
        db.rollback()
        return _err(exc, db)
    db.commit()
    return {"entry": tc.entry_out(entry, _names(db), orgsvc.zone(org), now_utc())}


def _review(entry_id, body: ReviewIn, approve: bool, request: Request, principal: Principal, db: Session):
    org = orgsvc.get_org(db)
    try:
        entry = tc.review(db, principal, org, entry_id, approve, body.note, client_ip(request))
    except tc.TimeError as exc:
        db.rollback()
        return _err(exc, db)
    db.commit()
    return {"entry": tc.entry_out(entry, _names(db), orgsvc.zone(org), now_utc())}


@router.post("/entries/{entry_id}/approve")
def do_approve(
    entry_id: uuid.UUID,
    body: ReviewIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    return _review(entry_id, body, True, request, principal, db)


@router.post("/entries/{entry_id}/reject")
def do_reject(
    entry_id: uuid.UUID,
    body: ReviewIn,
    request: Request,
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    return _review(entry_id, body, False, request, principal, db)


@router.get("/entries")
def list_entries(
    user_id: uuid.UUID | None = None,
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    org = orgsvc.get_org(db)
    tz = orgsvc.zone(org)
    start, end = _period(org, start, end)
    scope = _users_scope(db, principal, org, user_id)
    now = now_utc()
    names = _names(db)
    rows = tc.entries_between(db, tz, start, end, scope)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "timezone": org.timezone,
        "entries": [tc.entry_out(e, names, tz, now) for e in reversed(rows)],
    }


@router.get("/summary")
def summary(
    user_id: uuid.UUID | None = None,
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    org = orgsvc.get_org(db)
    tz = orgsvc.zone(org)
    start, end = _period(org, start, end)
    scope = _users_scope(db, principal, org, user_id)
    now = now_utc()
    rows = tc.entries_between(db, tz, start, end, scope)
    names = _names(db)
    by_user: dict[uuid.UUID, list] = {}
    for e in rows:
        by_user.setdefault(e.user_id, []).append(e)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "timezone": org.timezone,
        **tc.summarize(rows, tz, start, end, now),
        "people": [
            {"user_id": str(uid), "user_name": names.get(uid, "—"), **tc.summarize(es, tz, start, end, now)["totals"]}
            for uid, es in sorted(by_user.items(), key=lambda kv: names.get(kv[0], ""))
        ],
    }


@router.get("/export")
def export(
    format: str = Query(default="csv", pattern="^(csv|xlsx)$"),
    user_id: uuid.UUID | None = None,
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    org = orgsvc.get_org(db)
    tz = orgsvc.zone(org)
    start, end = _period(org, start, end)
    scope = _users_scope(db, principal, org, user_id)
    now = now_utc()
    names = _names(db)
    rows = tc.entries_between(db, tz, start, end, scope)
    who = ", ".join(sorted(names.get(u, "—") for u in scope)) if scope else "toda la organización"
    head = tc.preamble(org, start, end, who, now)
    data = tc.export_rows(rows, names, tz, now)
    base = f"control-horario-{start.isoformat()}-{end.isoformat()}"
    if format == "csv":
        return Response(
            tc.to_csv(head, data),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base}.csv"'},
        )
    content = tc.to_xlsx(org, head, data, tc.summarize(rows, tz, start, end, now))
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{base}.xlsx"'},
    )


@router.get("/events")
def events(
    user_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_password_ok),
    db: Session = Depends(get_db),
):
    """Historial verificable (usuario, hora del servidor, tipo, origen y entrada de auditoría)."""
    org = orgsvc.get_org(db)
    scope = _users_scope(db, principal, org, user_id)
    q = select(TimeEvent)
    if scope is not None:
        q = q.where(TimeEvent.user_id.in_(scope))
    names = _names(db)
    return [tc.event_out(ev, names) for ev in db.execute(q.order_by(TimeEvent.id.desc()).limit(limit)).scalars()]


@router.get("/incidents")
def incidents(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    """Jornadas que requieren atención: abiertas demasiado tiempo, pendientes de cierre, cierres no registrados
    en el momento (automáticos, declarados o fijados) sin revisar e intentos de entrada duplicada."""
    org = orgsvc.get_org(db)
    tz = orgsvc.zone(org)
    now = now_utc()
    names = _names(db)
    approver = principal.id in tc.approvers(db, org)
    scope_q = select(TimeEntry)
    if not approver:
        scope_q = scope_q.where(TimeEntry.user_id == principal.id)
    max_hours = orgsvc.time_settings(org)["max_open_hours"]
    open_rows = db.execute(scope_q.where(TimeEntry.clock_out_at.is_(None))).scalars().all()
    unusual = db.execute(
        scope_q.where(
            TimeEntry.close_kind.in_(("auto", "declared", "admin")), TimeEntry.approval_status == "pending"
        ).order_by(TimeEntry.clock_in_at.desc())
    ).scalars()
    dup_q = select(TimeEvent).where(TimeEvent.kind == "duplicate_blocked", TimeEvent.at > now - timedelta(days=30))
    if not approver:
        dup_q = dup_q.where(TimeEvent.user_id == principal.id)
    return {
        "can_approve": approver,
        "open_too_long": [
            tc.entry_out(e, names, tz, now) for e in open_rows if now - e.clock_in_at > timedelta(hours=max_hours)
        ],
        "pending_close": [tc.entry_out(e, names, tz, now) for e in open_rows if e.pending_since],
        "needs_review": [tc.entry_out(e, names, tz, now) for e in unusual],
        "duplicates": [
            tc.event_out(ev, names) for ev in db.execute(dup_q.order_by(TimeEvent.id.desc()).limit(50)).scalars()
        ],
    }
