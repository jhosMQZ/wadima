"""Tareas periódicas del backend: barrido de entradas inactivas y heartbeat de agentes.

Los workers pesados (pipeline documental, Memory Agent, sincronización con Drive) se registran
aquí cuando están habilitados y se ejecutan en hilos para no bloquear el bucle de eventos.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from starlette.concurrency import run_in_threadpool

from wadima.config import get_settings
from wadima.db import session_scope
from wadima.realtime import hub
from wadima.services.visits import sweep_stale_visits

log = logging.getLogger("wadima.background")

_tasks: list[asyncio.Task] = []
_periodic: list[tuple[str, float, Callable[[], None]]] = []


def register_periodic(name: str, seconds: float, fn: Callable[[], None]) -> None:
    _periodic.append((name, seconds, fn))


def _sweep() -> None:
    settings = get_settings()
    with session_scope() as db:
        closed = sweep_stale_visits(db, hub.online_users(), settings.area_inactivity_minutes)
        if closed:
            log.info("Cerradas %s entradas a áreas por fin de sesión o inactividad", closed)


def _time_sweep() -> None:
    from wadima import timeclock

    with session_scope() as db:
        result = timeclock.sweep(db, hub.online_users())
        if any(result.values()):
            log.info("Control horario: %s", result)


async def _loop(name: str, seconds: float, fn: Callable[[], None]) -> None:
    await asyncio.sleep(2)
    while True:
        try:
            await run_in_threadpool(fn)
        except Exception:  # noqa: BLE001
            log.exception("Fallo en tarea periódica %s", name)
        await asyncio.sleep(seconds)


def start() -> None:
    register_periodic("visit-sweeper", 60, _sweep)
    register_periodic("time-sweeper", 60, _time_sweep)
    if get_settings().embedded_workers:
        from wadima.workers import register_workers

        register_workers(register_periodic)
    for name, seconds, fn in _periodic:
        _tasks.append(asyncio.create_task(_loop(name, seconds, fn), name=name))


async def stop() -> None:
    for t in _tasks:
        t.cancel()
    for t in _tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t
    _tasks.clear()
    _periodic.clear()
