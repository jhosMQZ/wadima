"""Registro de errores: archivo rotativo en var/logs y búfer en memoria para el panel de diagnóstico."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections import deque
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from wadima.config import get_settings

_ring: deque[dict] = deque(maxlen=1000)
_SECRET_RE = re.compile(r"(sk-ant-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+|(ya29\.)[A-Za-z0-9_\-\.]+|(1//)[A-Za-z0-9_\-]+")


def redact(text: str) -> str:
    """Oculta API keys de Anthropic y tokens de Google que pudieran colarse en mensajes de log."""
    return _SECRET_RE.sub(lambda m: (m.group(1) or m.group(2) or m.group(3)) + "***", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if record.exc_info:
            data["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False)


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):  # nunca romper por el log
            _ring.append(json.loads(JsonFormatter().format(record)))


def setup_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_wadima", False):
        return
    settings = get_settings()
    root.setLevel(logging.INFO)
    if not settings.is_test:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(settings.logs_dir / "backend.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(JsonFormatter())
        root.addHandler(fh)
    ring = RingHandler(level=logging.INFO)
    root.addHandler(ring)
    for noisy in ("httpx", "httpcore", "watchfiles", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._wadima = True  # type: ignore[attr-defined]


def recent_logs(level: str | None = None, limit: int = 200) -> list[dict]:
    items = list(_ring)
    if level:
        items = [i for i in items if i["level"] == level.upper()]
    return items[-limit:][::-1]
