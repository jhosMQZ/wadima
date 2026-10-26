"""Almacén local de originales direccionado por contenido (SHA-256). Los originales nunca se modifican."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from wadima.config import get_settings


def blob_relpath(sha256: str) -> str:
    return f"{sha256[:2]}/{sha256[2:4]}/{sha256}"


def blob_path(rel: str) -> Path:
    root = get_settings().blobs_dir.resolve()
    path = (root / rel).resolve()
    if root not in path.parents:
        raise ValueError("Ruta de blob fuera del almacén")
    return path


def store_blob(content: bytes) -> tuple[str, str]:
    """Guarda el contenido (escritura atómica) y devuelve (sha256, ruta relativa)."""
    sha = hashlib.sha256(content).hexdigest()
    rel = blob_relpath(sha)
    path = blob_path(rel)
    if path.exists():
        return sha, rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return sha, rel


def read_blob(rel: str) -> bytes:
    data = blob_path(rel).read_bytes()
    return data
