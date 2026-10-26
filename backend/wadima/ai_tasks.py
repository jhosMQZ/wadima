"""Tareas de proyecto asignadas a la IA de un socio (su Claude conectado por MCP).

Asignar una tarea a «Claude de X» la deja en la cola de X: su Claude Code la recoge (get_task) cuando X lo pide y
entrega el resultado (submit_result). WADIMA no ejecuta nada con la cuenta de nadie: el dueño decide cuándo
atender su cola. Al recogerla, la tarea pasa a «en curso»; al entregarla, el resultado se publica como comentario
de la tarea (visible para el proyecto) y la tarea queda terminada, o bloqueada si la IA informa de un fallo.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wadima.activity import log_activity, notify
from wadima.models import AICredential, AIRun, Comment, MCPToken, Project, Task, User
from wadima.realtime import emit
from wadima.security.auth import now_utc

PROVIDER = "claude_mcp"
ACTIVE_DAYS = 14
OPEN = ("queued", "claimed")


def agent_name(owner: User) -> str:
    return f"Claude de {owner.display_name}"


def claude_status(db: Session, users: list[User]) -> dict[uuid.UUID, dict[str, Any]]:
    """Estado de la IA de cada socio: modo de conexión, cliente, cola y tarea en curso (sin datos de su cuenta)."""
    ids = [u.id for u in users]
    if not ids:
        return {}
    now = now_utc()
    tokens: dict[uuid.UUID, MCPToken] = {}
    for t in db.execute(
        select(MCPToken).where(MCPToken.user_id.in_(ids), MCPToken.revoked_at.is_(None)).order_by(MCPToken.created_at)
    ).scalars():
        best = tokens.get(t.user_id)
        if best is None or (t.last_used_at and (best.last_used_at is None or t.last_used_at > best.last_used_at)):
            tokens[t.user_id] = t
    creds = {
        c.user_id: c.status
        for c in db.execute(
            select(AICredential).where(AICredential.user_id.in_(ids), AICredential.provider == "anthropic_api")
        ).scalars()
    }
    pending = dict(
        db.execute(
            select(AIRun.user_id, func.count())
            .where(AIRun.user_id.in_(ids), AIRun.provider == PROVIDER, AIRun.status == "queued")
            .group_by(AIRun.user_id)
        ).all()
    )
    working: dict[uuid.UUID, AIRun] = {}
    for r in db.execute(
        select(AIRun)
        .where(
            AIRun.user_id.in_(ids),
            AIRun.provider == PROVIDER,
            AIRun.status == "claimed",
            AIRun.created_at > now - timedelta(days=7),
        )
        .order_by(AIRun.created_at)
    ).scalars():
        working[r.user_id] = r
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for u in users:
        tok = tokens.get(u.id)
        mcp_recent = bool(tok and tok.last_used_at and now - tok.last_used_at < timedelta(days=ACTIVE_DAYS))
        api_ok = creds.get(u.id) == "verified"
        run = working.get(u.id)
        client = (tok.last_client or {}) if tok else {}
        state = (
            "working" if run else "waiting" if pending.get(u.id) else "idle" if (mcp_recent or api_ok) else "offline"
        )
        out[u.id] = {
            "name": agent_name(u),
            "connected": mcp_recent or api_ok,
            "subscription": mcp_recent,
            "api": api_ok,
            "assignable": bool(tok and tok.last_used_at),
            "client": f"{client.get('name', '')} {client.get('version', '')}".strip() or None,
            "last_used_at": tok.last_used_at.isoformat() if tok and tok.last_used_at else None,
            "pending": int(pending.get(u.id, 0)),
            "state": state,
            "working_task_id": str(run.task_id) if run and run.task_id else None,
            "working_run_id": str(run.id) if run else None,
        }
    return out


def check_assignable(db: Session, owner_id: uuid.UUID, area_id: uuid.UUID) -> User:
    from wadima.models import AreaMember

    owner = db.get(User, owner_id)
    member = db.execute(
        select(AreaMember.id).where(AreaMember.area_id == area_id, AreaMember.user_id == owner_id)
    ).first()
    if owner is None or not owner.is_active or (not member and owner.role != "admin"):
        raise ValueError("La IA elegida pertenece a alguien que no es miembro del área")
    used = db.execute(
        select(MCPToken.id).where(
            MCPToken.user_id == owner_id, MCPToken.revoked_at.is_(None), MCPToken.last_used_at.is_not(None)
        )
    ).first()
    if not used:
        raise ValueError(
            f"{owner.display_name} todavía no ha conectado su Claude (Configuración de IA → token MCP y Claude Code)"
        )
    return owner


def queue_for_task(db: Session, task: Task, project: Project, owner: User, assigned_by: User) -> AIRun:
    cancel_open_runs(db, task, reason="reasignada")
    prompt = (
        f"Tarea del proyecto «{project.name}»: {task.title}\n\n"
        + (f"{task.description}\n\n" if task.description else "")
        + "Entrega el resultado en Markdown. Si no puedes completarla, explica qué falta."
    )
    run = AIRun(
        user_id=owner.id,
        area_id=project.area_id,
        project_id=project.id,
        task_id=task.id,
        provider=PROVIDER,
        model="suscripción (se elige en Claude)",
        prompt=prompt,
        status="queued",
        usage={"use_memory": True, "assigned_by": str(assigned_by.id), "assigned_by_name": assigned_by.display_name},
    )
    db.add(run)
    db.flush()
    log_activity(
        db,
        "ai.assigned",
        f"Asignó la tarea «{task.title}» a {agent_name(owner)}",
        user=assigned_by,
        area_id=project.area_id,
        project_id=project.id,
        ref_type="task",
        ref_id=task.id,
    )
    notify(
        db,
        {owner.id},
        "ai.task",
        f"Tarea para tu Claude: {task.title}",
        f"Asignada por {assigned_by.display_name} en «{project.name}». "
        "Pide a Claude Code que atienda tus tareas de WADIMA.",
        link="/ai",
    )
    emit(db, "ai.changed", everyone=True)
    return run


def cancel_open_runs(db: Session, task: Task, reason: str) -> int:
    n = 0
    for run in db.execute(
        select(AIRun).where(AIRun.task_id == task.id, AIRun.provider == PROVIDER, AIRun.status.in_(OPEN))
    ).scalars():
        run.status = "cancelled"
        run.error = f"Tarea {reason}"
        run.finished_at = now_utc()
        n += 1
    if n:
        emit(db, "ai.changed", everyone=True)
    return n


def on_claimed(db: Session, run: AIRun) -> None:
    task = db.get(Task, run.task_id) if run.task_id else None
    if task is None:
        return
    owner = db.get(User, run.user_id)
    if task.status == "todo":
        task.status = "in_progress"
        task.started_at = task.started_at or now_utc()
        task.version += 1
    log_activity(
        db,
        "ai.started",
        f"{agent_name(owner)} empezó la tarea «{task.title}»",
        actor_kind="agent",
        actor_name=agent_name(owner),
        area_id=run.area_id,
        project_id=run.project_id,
        ref_type="task",
        ref_id=task.id,
    )
    emit(db, "task.changed", area_id=run.area_id, project_id=str(run.project_id), id=str(task.id))


def on_delivered(db: Session, run: AIRun, failed: bool) -> None:
    """Publica el resultado en la tarea y la cierra (o la bloquea si la IA no pudo completarla)."""
    task = db.get(Task, run.task_id) if run.task_id else None
    if task is None:
        return
    owner = db.get(User, run.user_id)
    name = agent_name(owner)
    header = f"🤖 **{name}** {'no pudo completar la tarea' if failed else 'entregó el resultado'}:\n\n"
    db.add(
        Comment(
            area_id=run.area_id,
            target_type="task",
            target_id=task.id,
            author_id=owner.id,
            body=(header + run.output)[:100_000],
        )
    )
    run.shared = True  # el resultado de una tarea de proyecto es visible para el proyecto
    now = now_utc()
    task.status = "blocked" if failed else "done"
    if not failed:
        task.completed_at = now
    task.version += 1
    log_activity(
        db,
        "task.blocked" if failed else "task.completed",
        f"{name} {'bloqueó' if failed else 'terminó'} la tarea «{task.title}»",
        actor_kind="agent",
        actor_name=name,
        area_id=run.area_id,
        project_id=run.project_id,
        ref_type="task",
        ref_id=task.id,
    )
    assigned_by = (run.usage or {}).get("assigned_by")
    to_notify = {uuid.UUID(assigned_by)} if assigned_by else set()
    if task.created_by:
        to_notify.add(task.created_by)
    if task.assignee_id:
        to_notify.add(task.assignee_id)
    to_notify.discard(owner.id)
    notify(
        db,
        to_notify,
        "ai.result",
        f"{name} {'no pudo completar' if failed else 'terminó'}: {task.title}",
        link=f"/projects/{task.project_id}",
    )
    from wadima.services import jobs

    jobs.enqueue(
        db, "memory.ingest", {"source_type": "ai_run", "source_id": str(run.id)}, dedupe_key=f"memory:ai_run:{run.id}"
    )
    emit(db, "task.changed", area_id=run.area_id, project_id=str(run.project_id), id=str(task.id))
    emit(db, "comment.changed", area_id=run.area_id, target_type="task", target_id=str(task.id))
