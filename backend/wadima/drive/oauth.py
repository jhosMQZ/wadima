"""Flujo OAuth 2.0 (código de autorización + PKCE) con Google. Los tokens se guardan cifrados."""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from wadima.config import get_settings
from wadima.drive.client import DriveAuthError, DriveError, Tokens, http_client
from wadima.models import DriveConnection, OAuthState
from wadima.security.auth import now_utc
from wadima.security.crypto import decrypt_secret, encrypt_secret, random_token, sha256_hex

DRIVE_SCOPE_FILE = "https://www.googleapis.com/auth/drive.file"
DRIVE_SCOPE_FULL = "https://www.googleapis.com/auth/drive"


def configured() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret)


def scopes() -> str:
    return DRIVE_SCOPE_FULL if get_settings().google_full_scope else DRIVE_SCOPE_FILE


def start_flow(db: Session, user_id: uuid.UUID, purpose: str = "workspace") -> str:
    """Crea un estado OAuth de un solo uso y devuelve la URL de consentimiento de Google."""
    s = get_settings()
    state = random_token(24)
    verifier = random_token(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    db.add(
        OAuthState(
            state_hash=sha256_hex(state),
            provider="google",
            purpose=purpose,
            user_id=user_id,
            verifier_encrypted=encrypt_secret(verifier),
            expires_at=now_utc() + timedelta(minutes=10),
        )
    )
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": s.google_redirect_uri,
        "response_type": "code",
        "scope": scopes(),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{s.google_auth_url}?{urlencode(params)}"


def consume_state(db: Session, state: str, user_id: uuid.UUID) -> tuple[OAuthState, str]:
    row = db.execute(
        select(OAuthState).where(OAuthState.state_hash == sha256_hex(state)).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= now_utc() or row.user_id != user_id:
        raise DriveAuthError("Estado OAuth inválido, caducado o de otro usuario")
    row.used_at = now_utc()
    return row, decrypt_secret(row.verifier_encrypted)


def _token_request(data: dict) -> dict:
    s = get_settings()
    with http_client(30) as http:
        resp = http.post(f"{s.google_oauth_base}/token", data=data)
    if resp.status_code >= 400:
        try:
            err = resp.json()
            msg = f"{err.get('error')}: {err.get('error_description', '')}"
        except ValueError:
            msg = resp.text[:200]
        if resp.status_code in (400, 401) and "invalid_grant" in msg:
            raise DriveAuthError(f"Google rechazó la autorización ({msg})", resp.status_code)
        raise DriveError(
            f"Error en el intercambio de tokens con Google ({msg})", resp.status_code, retryable=resp.status_code >= 500
        )
    return resp.json()


def exchange_code(code: str, verifier: str) -> Tokens:
    s = get_settings()
    data = _token_request(
        {
            "code": code,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "redirect_uri": s.google_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }
    )
    return Tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + int(data.get("expires_in", 3600)),
        scope=data.get("scope", ""),
    )


def refresh_tokens(tokens: Tokens) -> Tokens:
    s = get_settings()
    data = _token_request(
        {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "refresh_token": tokens.refresh_token,
            "grant_type": "refresh_token",
        }
    )
    return Tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", tokens.refresh_token),
        expires_at=time.time() + int(data.get("expires_in", 3600)),
        scope=data.get("scope", tokens.scope),
    )


def revoke(tokens: Tokens) -> bool:
    s = get_settings()
    token = tokens.refresh_token or tokens.access_token
    with http_client(30) as http:
        resp = http.post(f"{s.google_oauth_base}/revoke", data={"token": token})
    return resp.status_code == 200


def load_tokens(conn: DriveConnection) -> Tokens:
    if not conn.token_encrypted:
        raise DriveAuthError("La conexión no tiene tokens")
    return Tokens.from_json(decrypt_secret(conn.token_encrypted))


def store_tokens(conn: DriveConnection, tokens: Tokens) -> None:
    conn.token_encrypted = encrypt_secret(tokens.to_json())
    conn.granted_scopes = tokens.scope
