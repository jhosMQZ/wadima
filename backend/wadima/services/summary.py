"""Resúmenes de actividad construidos exclusivamente a partir de eventos registrados."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta, tzinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima.models import ActivityEvent, Project
from wadima.security.auth import now_utc

SECTIONS = [
    ("changes", "Cambios realizados", ("project.", "note.", "decision.", "studio.", "comment.")),
    ("tasks_started", "Tareas iniciadas", ("task.started",)),
    ("tasks_done", "Tareas terminadas", ("task.completed",)),
    ("tasks_created", "Tareas creadas", ("task.created",)),
    ("documents", "Documentos añadidos o modificados", ("document.added", "document.version", "document.converted")),
    ("agents", "Resultados de agentes", ("agent.", "ai.")),
    ("errors", "Errores y bloqueos", ("task.blocked", "document.error", "sync.error", "error.")),
]


def _section_for(kind: str) -> str | None:
    for key, _title, prefixes in SECTIONS:
        if any(kind == p or (p.endswith(".") and kind.startswith(p)) for p in prefixes):
            return key
    return None


def build_summary(
    events: list[ActivityEvent],
    since: datetime,
    until: datetime,
    *,
    tz: tzinfo = UTC,
    org_name: str | None = None,
) -> dict:
    """Las horas se muestran en la zona horaria de la organización; el encabezado lleva su nombre."""
    grouped: dict[str, list[ActivityEvent]] = defaultdict(list)
    per_actor: Counter[str] = Counter()
    for ev in events:
        per_actor[ev.actor_name or ev.actor_kind] += 1
        key = _section_for(ev.kind)
        if key:
            grouped[key].append(ev)
    zone_name = getattr(tz, "key", None) or str(tz)
    title = f"{org_name} · Resumen de actividad" if org_name else "Resumen de actividad"
    a, b = since.astimezone(tz), until.astimezone(tz)
    lines = [f"## {title} ({a:%Y-%m-%d %H:%M} – {b:%Y-%m-%d %H:%M}, {zone_name})", ""]
    if not events:
        lines.append("_No se registró actividad en este periodo._")
    for key, title, _ in SECTIONS:
        items = grouped.get(key, [])
        if not items:
            continue
        lines.append(f"### {title} ({len(items)})")
        for ev in items[:25]:
            lines.append(f"- {ev.ts.astimezone(tz):%Y-%m-%d %H:%M} · {ev.actor_name}: {ev.summary}")
        if len(items) > 25:
            lines.append(f"- … y {len(items) - 25} más")
        lines.append("")
    if per_actor:
        lines.append("### Actividad por persona o agente")
        for actor, n in per_actor.most_common():
            lines.append(f"- {actor}: {n} evento(s)")
    return {
        "markdown": "\n".join(lines).strip() + "\n",
        "counts": {key: len(grouped.get(key, [])) for key, _, _ in SECTIONS},
        "per_actor": dict(per_actor),
        "events": len(events),
        "since": since.isoformat(),
        "until": until.isoformat(),
    }


def _build(db: Session, events: list[ActivityEvent], since: datetime, until: datetime) -> dict:
    from wadima import org as orgsvc

    org = orgsvc.get_org(db)
    return build_summary(events, since, until, tz=orgsvc.zone(org), org_name=org.legal_name)


def project_summary(db: Session, project: Project, since: datetime | None = None) -> dict:
    until = now_utc()
    since = since or project.last_summary_at or (until - timedelta(days=7))
    events = list(
        db.execute(
            select(ActivityEvent)
            .where(ActivityEvent.project_id == project.id, ActivityEvent.ts > since, ActivityEvent.ts <= until)
            .order_by(ActivityEvent.ts)
        ).scalars()
    )
    return _build(db, events, since, until)


def area_summary(db: Session, area_id: uuid.UUID, days: int = 7) -> dict:
    until = now_utc()
    since = until - timedelta(days=days)
    events = list(
        db.execute(
            select(ActivityEvent)
            .where(ActivityEvent.area_id == area_id, ActivityEvent.ts > since)
            .order_by(ActivityEvent.ts)
        ).scalars()
    )
    return _build(db, events, since, until)
