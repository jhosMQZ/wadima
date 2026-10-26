"""Hash de contraseñas, tokens aleatorios y cifrado de secretos en reposo.

La clave maestra (Fernet) protege tokens OAuth y API keys guardados en PostgreSQL. Se obtiene de
WADIMA_MASTER_KEY o se genera una vez y se guarda en data_dir/secrets protegida con DPAPI
(ámbito del usuario de Windows). Nunca se sincroniza con Drive ni se versiona.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sys
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from wadima.config import get_settings

_hasher = PasswordHasher()  # argon2id con parámetros por defecto recomendados


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str | bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# --------------------------------------------------------------------------- DPAPI (Windows)
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI falló al " + ("proteger" if protect else "desproteger") + " la clave")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _load_or_create_master_key(secrets_dir: Path) -> bytes:
    secrets_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        path = secrets_dir / "master.key.dpapi"
        if path.exists():
            return _dpapi(path.read_bytes(), protect=False)
        key = Fernet.generate_key()
        path.write_bytes(_dpapi(key, protect=True))
        return key
    path = secrets_dir / "master.key"
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    if settings.master_key:
        raw = settings.master_key.encode()
        try:
            return Fernet(raw)
        except ValueError:
            # Permitir una frase arbitraria derivando una clave de 32 bytes.
            return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))
    return Fernet(_load_or_create_master_key(settings.secrets_dir))


def encrypt_secret(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(token: bytes) -> str:
    try:
        return _fernet().decrypt(bytes(token)).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("No se pudo descifrar el secreto (¿cambió la clave maestra?)") from exc


def reset_crypto_cache() -> None:
    _fernet.cache_clear()
