"""Diagnóstico para administradores: auditoría, logs, cola de trabajos y estado de servicios."""

from __future__ import annotations

import platform
import shutil
import sys

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from wadima import audit
from wadima.api.serial import iso
from wadima.config import get_settings
from wadima.db import get_db
from wadima.logs import recent_logs
from wadima.models import AgentState, AuditLog, Job
from wadima.realtime import hub
from wadima.security.auth import Principal, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/audit")
def audit_entries(
    limit: int = 100,
    action: str | None = None,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))
    if action:
        q = q.where(AuditLog.action == action)
    return [
        {
            "id": a.id,
            "ts": iso(a.ts),
            "actor_id": str(a.actor_id) if a.actor_id else None,
            "action": a.action,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "area_id": str(a.area_id) if a.area_id else None,
            "ip": a.ip,
            "data": a.data,
            "hash": a.hash,
            "prev_hash": a.prev_hash,
        }
        for a in db.execute(q).scalars()
    ]


@router.get("/audit/verify")
def audit_verify(admin: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return audit.verify_chain(db)


@router.get("/logs")
def logs(level: str | None = None, limit: int = 200, admin: Principal = Depends(require_admin)):
    return recent_logs(level, min(limit, 1000))


@router.get("/jobs")
def jobs(
    status: str | None = None,
    limit: int = 100,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = select(Job).order_by(Job.id.desc()).limit(min(limit, 500))
    if status:
        q = q.where(Job.status == status)
    counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    return {
        "counts": counts,
        "jobs": [
            {
                "id": j.id,
                "kind": j.kind,
                "status": j.status,
                "attempts": j.attempts,
                "max_attempts": j.max_attempts,
                "last_error": j.last_error[-2000:],
                "created_at": iso(j.created_at),
                "finished_at": iso(j.finished_at),
                "run_after": iso(j.run_after),
                "payload": j.payload,
            }
            for j in db.execute(q).scalars()
        ],
    }


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, admin: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job and job.status in ("error", "dead"):
        job.status = "queued"
        job.attempts = 0
        job.run_after = func.now()
        db.commit()
    return {"ok": True}


@router.get("/system")
def system(admin: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    s = get_settings()
    db_size = db.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))")).scalar_one()
    pg_version = db.execute(text("SHOW server_version")).scalar_one()
    disk = shutil.disk_usage(s.data_dir if s.data_dir.exists() else ".")
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "postgres": pg_version,
        "database_size": db_size,
        "data_dir": str(s.data_dir),
        "disk_free_gb": round(disk.free / 1e9, 1),
        "online_users": len(hub.online_users()),
        "agents": [
            {"key": a.key, "status": a.status, "heartbeat_at": iso(a.heartbeat_at), "current": a.current}
            for a in db.execute(select(AgentState)).scalars()
        ],
        "tools": {
            name: bool(shutil.which(name))
            for name in ("git", "node", "npm", "pnpm", "python", "docker", "podman", "tesseract", "claude", "tailscale")
        },
    }
