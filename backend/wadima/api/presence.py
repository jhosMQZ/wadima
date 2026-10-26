"""Estado de la oficina virtual (personas, salas, agentes) y estado declarado del usuario."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from wadima import org as orgsvc
from wadima import timeclock as tc
from wadima.api.serial import area_out, iso, user_public
from wadima.db import SessionLocal, get_db
from wadima.meetings import MeetingError, ice_servers, meetings
from wadima.models import (
    ActivityEvent,
    AgentState,
    AICredential,
    Area,
    AreaMember,
    AreaVisit,
    Job,
    MCPToken,
    Notification,
    Project,
    ProjectMember,
    Task,
    TimeEntry,
    User,
    UserStatus,
)
from wadima.office import ROOM_KEYS, ROOMS
from wadima.realtime import Connection, emit, hub
from wadima.security.auth import COOKIE, Principal, check_origin, now_utc, require_password_ok, resolve_session
from wadima.security.perms import accessible_area_ids

router = APIRouter(prefix="/api", tags=["office"])
log = logging.getLogger("wadima.presence")

ROOM_PATTERN = "^(" + "|".join(ROOM_KEYS) + ")$"


class StatusIn(BaseModel):
    declared: str = Field(pattern="^(available|working|meeting|paused)$")
    message: str = Field(default="", max_length=200)
    task_id: uuid.UUID | None = None
    room_key: str | None = Field(default=None, pattern=ROOM_PATTERN)


def presence_state(online: bool, declared: str | None) -> str:
    if not online:
        return "offline"
    return {"working": "working", "meeting": "meeting", "paused": "paused"}.get(declared or "", "online")


@router.get("/office/state")
def office_state(principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    area_ids = set(accessible_area_ids(db, principal))
    areas = db.execute(select(Area).where(Area.id.in_(area_ids))).scalars().all() if area_ids else []
    online = hub.online_users()
    week_ago = now_utc() - timedelta(days=7)

    unread = db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == principal.id, Notification.read_at.is_(None))
    ).scalar_one()
    org = orgsvc.get_org(db)
    from wadima import ai_tasks

    active_users = db.execute(select(User).where(User.is_active)).scalars().all()
    claude = ai_tasks.claude_status(db, list(active_users))
    open_entries = {
        e.user_id: e for e in db.execute(select(TimeEntry).where(TimeEntry.clock_out_at.is_(None))).scalars()
    }
    people = []
    for user in db.execute(select(User).where(User.is_active).order_by(User.created_at, User.username)).scalars():
        st = db.get(UserStatus, user.id)
        is_online = user.id in online
        visible_area = st.area_id if st and st.area_id in area_ids else None
        task = db.get(Task, st.task_id) if st and st.task_id else None
        task_info = None
        if task is not None:
            proj = db.get(Project, task.project_id)
            if proj and proj.area_id in area_ids:
                task_info = {
                    "id": str(task.id),
                    "title": task.title,
                    "status": task.status,
                    "project_id": str(proj.id),
                    "project_name": proj.name,
                }
        last_ev = None
        if area_ids:
            last_ev = db.execute(
                select(ActivityEvent)
                .where(ActivityEvent.user_id == user.id, ActivityEvent.area_id.in_(area_ids))
                .order_by(ActivityEvent.ts.desc())
                .limit(1)
            ).scalar_one_or_none()
        projects = []
        if area_ids:
            projects = [
                {"id": str(p.id), "name": p.name, "status": p.status}
                for p in db.execute(
                    select(Project)
                    .join(ProjectMember, ProjectMember.project_id == Project.id)
                    .where(
                        ProjectMember.user_id == user.id, Project.area_id.in_(area_ids), Project.status != "archived"
                    )
                    .limit(10)
                ).scalars()
            ]
        tasks_q = (
            (
                select(Task.status, func.count())
                .join(Project, Project.id == Task.project_id)
                .where(Task.assignee_id == user.id, Project.area_id.in_(area_ids))
                .group_by(Task.status)
            )
            if area_ids
            else None
        )
        by_status = dict(db.execute(tasks_q).all()) if tasks_q is not None else {}
        done_week = (
            db.execute(
                select(func.count())
                .select_from(Task)
                .join(Project, Project.id == Task.project_id)
                .where(Task.assignee_id == user.id, Task.completed_at >= week_ago, Project.area_id.in_(area_ids))
            ).scalar_one()
            if area_ids
            else 0
        )
        open_visit = db.execute(
            select(AreaVisit)
            .where(AreaVisit.user_id == user.id, AreaVisit.left_at.is_(None))
            .order_by(AreaVisit.entered_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        entry = open_entries.get(user.id)
        clock = None
        if entry is not None and tc.can_view_user(db, principal, org, user.id):
            clock = {
                "status": tc.entry_status(entry),
                "clock_in_at": iso(entry.clock_in_at),
                "running_seconds": tc.duration_seconds(entry, now_utc()),
            }
        ai = dict(claude.get(user.id, {}))
        working_task = db.get(Task, uuid.UUID(ai["working_task_id"])) if ai.get("working_task_id") else None
        working_project = db.get(Project, working_task.project_id) if working_task else None
        # Solo se muestra el título si la tarea es de un área que quien mira puede ver.
        ai["working_on"] = (
            {"title": working_task.title, "project_id": str(working_project.id), "project_name": working_project.name}
            if working_task and working_project and working_project.area_id in area_ids
            else None
        )
        people.append(
            {
                **user_public(user),
                "clock": clock,
                "claude": ai,
                "online": is_online,
                "state": presence_state(is_online, st.declared if st else None),
                "declared": st.declared if st else "available",
                "message": st.message if st else "",
                "room_key": (st.room_key if st else None) if is_online else None,
                "area_id": str(visible_area) if visible_area else None,
                "area_entered_at": iso(open_visit.entered_at)
                if open_visit and open_visit.area_id in area_ids
                else None,
                "task": task_info,
                "last_seen_at": iso(st.last_seen_at) if st else None,
                "last_activity": {"summary": last_ev.summary, "ts": iso(last_ev.ts), "kind": last_ev.kind}
                if last_ev
                else None,
                "projects": projects,
                "work_summary": {
                    "todo": by_status.get("todo", 0),
                    "in_progress": by_status.get("in_progress", 0),
                    "blocked": by_status.get("blocked", 0),
                    "done_last_7_days": done_week,
                },
                "status_updated_at": iso(st.updated_at) if st else None,
                "is_me": user.id == principal.id,
                "unread_notifications": unread if user.id == principal.id else 0,
            }
        )

    agents = [
        {
            "key": a.key,
            "name": a.name,
            "status": a.status,
            "current": a.current,
            "last_result": a.last_result,
            "stats": a.stats,
            "heartbeat_at": iso(a.heartbeat_at),
            "updated_at": iso(a.updated_at),
        }
        for a in db.execute(select(AgentState).order_by(AgentState.key)).scalars()
    ]
    claude = [
        {"user_id": str(c.user_id), "status": c.status, "verified_at": iso(c.verified_at)}
        for c in db.execute(select(AICredential).where(AICredential.provider == "anthropic_api")).scalars()
    ]
    templates = {t["room_key"]: t for t in orgsvc.templates_out(db, org.id)}
    rooms = [
        {
            "key": k,
            **v,
            "enabled": templates[k]["enabled"] if k in templates else True,
            "template_area_id": templates[k]["area_id"] if k in templates else None,
            "areas": [area_out(a) for a in areas if a.room_key == k and a.enabled],
        }
        for k, v in ROOMS.items()
    ]
    return {
        "org": orgsvc.org_public(org),
        "rooms": rooms,
        "people": people,
        "agents": agents,
        "claude_connections": claude,
        "integrations": integrations_status(db),
        "server_time": iso(now_utc()),
    }


def integrations_status(db: Session) -> list[dict]:
    """Estado comprobado de cada integración (lo que muestran los equipos de la sala de servidores).
    ok = funcionando, warn = atención (pendientes, sin configurar), error = fallo, off = no configurada."""
    from wadima.api.drive import drive_summary

    out: list[dict] = [{"key": "database", "label": "Base de datos", "status": "ok", "detail": "PostgreSQL responde"}]
    drive = drive_summary(db)
    drive_status = {
        "synced": "ok",
        "pending": "warn",
        "conflict": "warn",
        "error": "error",
        "disconnected": "off",
        "not_configured": "off",
    }[drive["status"]]
    pending = drive["counts"].get("pending", 0) + drive["counts"].get("uploading", 0)
    detail = {
        "synced": "Sincronizado",
        "pending": f"{pending} archivo(s) pendientes",
        "conflict": "Hay conflictos por resolver",
        "error": drive["last_error"] or "Errores de sincronización",
        "disconnected": "No conectado",
        "not_configured": "Sin credenciales de Google",
    }[drive["status"]]
    out.append({"key": "drive", "label": "Google Drive", "status": drive_status, "detail": detail})
    creds = db.execute(select(AICredential.status).where(AICredential.provider == "anthropic_api")).scalars().all()
    verified = sum(1 for c in creds if c == "verified")
    failing = sum(1 for c in creds if c == "error")
    out.append(
        {
            "key": "claude_api",
            "label": "Claude (API)",
            "status": "error" if failing else "ok" if verified else "off",
            "detail": f"{verified} clave(s) verificada(s)" + (f", {failing} con error" if failing else ""),
        }
    )
    since = now_utc() - timedelta(days=14)
    mcp_users = db.execute(
        select(func.count(func.distinct(MCPToken.user_id))).where(
            MCPToken.revoked_at.is_(None), MCPToken.last_used_at >= since
        )
    ).scalar_one()
    out.append(
        {
            "key": "claude_mcp",
            "label": "Claude (suscripción vía MCP)",
            "status": "ok" if mcp_users else "off",
            "detail": f"{mcp_users} socio(s) con cliente conectado en 14 días",
        }
    )
    failed = db.execute(select(func.count()).select_from(Job).where(Job.status.in_(("error", "dead")))).scalar_one()
    queued = db.execute(select(func.count()).select_from(Job).where(Job.status.in_(("queued", "running")))).scalar_one()
    out.append(
        {
            "key": "jobs",
            "label": "Trabajos en segundo plano",
            "status": "error" if failed else "warn" if queued else "ok",
            "detail": f"{queued} en cola, {failed} con error",
        }
    )
    online = len(hub.online_users())
    out.append({"key": "realtime", "label": "Tiempo real", "status": "ok", "detail": f"{online} socio(s) conectados"})
    return out


@router.put("/me/status")
def set_status(body: StatusIn, principal: Principal = Depends(require_password_ok), db: Session = Depends(get_db)):
    st = db.get(UserStatus, principal.id)
    if st is None:
        st = UserStatus(user_id=principal.id)
        db.add(st)
    if body.task_id is not None:
        task = db.get(Task, body.task_id)
        proj = db.get(Project, task.project_id) if task else None
        if not proj or proj.area_id not in set(accessible_area_ids(db, principal)):
            raise HTTPException(404, "Tarea no encontrada")
    st.declared = body.declared
    st.message = body.message
    st.task_id = body.task_id
    if body.room_key is not None:
        st.room_key = body.room_key
    emit(db, "presence.changed", everyone=True, user_id=str(principal.id))
    db.commit()
    return {"ok": True, "state": presence_state(True, st.declared)}


# --------------------------------------------------------------------------- WebSocket
def _ws_auth(token: str | None) -> Principal | None:
    db = SessionLocal()
    try:
        p = resolve_session(db, token)
        if p is None or p.user.must_change_password:
            return None
        db.expunge_all()
        return p
    finally:
        db.close()


def _mark_seen(user_id: uuid.UUID, announce: bool) -> None:
    db = SessionLocal()
    try:
        st = db.get(UserStatus, user_id)
        if st is None:
            st = UserStatus(user_id=user_id)
            db.add(st)
        st.last_seen_at = now_utc()
        if announce:
            emit(db, "presence.changed", everyone=True, user_id=str(user_id))
        db.commit()
    finally:
        db.close()


async def websocket_endpoint(ws: WebSocket) -> None:
    if not check_origin(ws.headers.get("origin")):
        await ws.close(code=4403)
        return
    principal = await run_in_threadpool(_ws_auth, ws.cookies.get(COOKIE))
    if principal is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    conn = Connection(ws=ws, user_id=principal.user.id, session_id=principal.session.id)
    first = hub.add(conn)
    await run_in_threadpool(_mark_seen, conn.user_id, first)
    await ws.send_json({"type": "hello", "user_id": str(conn.user_id)})
    pump = asyncio.create_task(hub.pump(conn))
    last_touch = asyncio.get_running_loop().time()
    try:
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "ping":
                now = asyncio.get_running_loop().time()
                if now - last_touch > 30:
                    last_touch = now
                    await run_in_threadpool(_mark_seen, conn.user_id, False)
                hub._put(conn, {"type": "pong"})
            elif isinstance(kind, str) and kind.startswith("meeting."):
                await _meeting_message(conn, kind, msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:  # noqa: BLE001
        log.exception("Error en WebSocket")
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        left = meetings.leave(conn)
        if left is not None:
            await run_in_threadpool(_meeting_presence, left.user_id, None, left.prev_declared)
        last = hub.remove(conn)
        await run_in_threadpool(_mark_seen, conn.user_id, last)


# --------------------------------------------------------------------------- reuniones (señalización WebRTC)
def _meeting_access(user_id: uuid.UUID, area_id: uuid.UUID) -> tuple[str, str] | None:
    """(nombre visible, estado declarado actual) si el socio puede entrar en la sala de reuniones del área."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        area = db.get(Area, area_id)
        if user is None or not user.is_active or area is None or area.archived_at is not None:
            return None
        if not area.enabled or area.room_key != "meeting":
            return None
        if (
            user.role != "admin"
            and not db.execute(
                select(AreaMember.id).where(AreaMember.area_id == area_id, AreaMember.user_id == user_id)
            ).first()
        ):
            return None
        st = db.get(UserStatus, user_id)
        return user.display_name, (st.declared if st else "available")
    finally:
        db.close()


def _meeting_presence(user_id: uuid.UUID, area_id: uuid.UUID | None, restore: str = "available") -> None:
    """Al entrar, el socio aparece «en reunión» en la sala de reuniones; al salir recupera su estado anterior."""
    db = SessionLocal()
    try:
        st = db.get(UserStatus, user_id)
        if st is None:
            st = UserStatus(user_id=user_id)
            db.add(st)
        if area_id is not None:
            st.declared = "meeting"
            st.room_key = "meeting"
        elif st.declared == "meeting" and not meetings.user_rooms(user_id):
            st.declared = restore if restore != "meeting" else "available"
        emit(db, "presence.changed", everyone=True, user_id=str(user_id))
        emit(db, "meeting.changed", everyone=True)
        db.commit()
    finally:
        db.close()


async def _meeting_message(conn: Connection, kind: str, msg: dict) -> None:
    try:
        if kind == "meeting.join":
            try:
                area_id = uuid.UUID(str(msg.get("area_id")))
            except ValueError:
                raise MeetingError("Sala no válida") from None
            access = await run_in_threadpool(_meeting_access, conn.user_id, area_id)
            if access is None:
                raise MeetingError("No tiene acceso a esta sala de reuniones")
            name, declared = access
            me, others = meetings.join(conn, area_id, name, declared)
            await run_in_threadpool(_meeting_presence, conn.user_id, area_id)
            hub._put(
                conn,
                {
                    "type": "meeting.joined",
                    "room_id": str(area_id),
                    "peer_id": me.peer_id,
                    "participants": [p.public() for p in others],
                    "ice_servers": ice_servers(conn.user_id),
                },
            )
        elif kind == "meeting.leave":
            left = meetings.leave(conn)
            if left is not None:
                await run_in_threadpool(_meeting_presence, conn.user_id, None, left.prev_declared)
                hub._put(conn, {"type": "meeting.left", "room_id": str(left.room_id)})
        elif kind == "meeting.signal":
            if not meetings.signal(conn, str(msg.get("to", "")), msg.get("data")):
                hub._put(conn, {"type": "meeting.error", "code": "peer_gone", "peer_id": msg.get("to")})
        elif kind == "meeting.state":
            meetings.set_muted(conn, bool(msg.get("muted", True)))
    except MeetingError as exc:
        hub._put(conn, {"type": "meeting.error", "code": "rejected", "message": str(exc), "request": kind})
