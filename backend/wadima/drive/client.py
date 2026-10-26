"""Cliente de la API REST oficial de Google Drive v3 (httpx), con renovación de token y reintentos.

No usa contraseñas de Google: solo tokens OAuth 2.0 obtenidos con consentimiento del usuario.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from wadima.config import get_settings

log = logging.getLogger("wadima.drive")

FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id,name,mimeType,md5Checksum,version,modifiedTime,trashed,parents,appProperties,size"
RESUMABLE_THRESHOLD = 5 * 1024 * 1024
CHUNK = 8 * 256 * 1024  # múltiplo de 256 KiB, como exige la API

# Permite inyectar un transporte simulado en pruebas (sin tocar la red).
TRANSPORT_OVERRIDE: httpx.BaseTransport | None = None
SLEEP: Callable[[float], None] = time.sleep


class DriveError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class DriveAuthError(DriveError):
    """El token no es válido o fue revocado: hay que volver a conectar."""


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: float
    scope: str = ""

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, raw: str) -> Tokens:
        return cls(**json.loads(raw))


def http_client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(transport=TRANSPORT_OVERRIDE, timeout=timeout, follow_redirects=False)


class DriveClient:
    def __init__(self, tokens: Tokens, on_refresh: Callable[[Tokens], None] | None = None, max_retries: int = 5):
        s = get_settings()
        self.tokens = tokens
        self.on_refresh = on_refresh
        self.max_retries = max_retries
        self.api = f"{s.google_api_base}/drive/v3"
        self.upload_api = f"{s.google_api_base}/upload/drive/v3"
        self.http = http_client()

    def close(self) -> None:
        self.http.close()

    # ------------------------------------------------------------------ auth
    def _refresh(self) -> None:
        from wadima.drive.oauth import refresh_tokens

        if not self.tokens.refresh_token:
            raise DriveAuthError("No hay refresh token; vuelva a conectar Google Drive")
        self.tokens = refresh_tokens(self.tokens)
        if self.on_refresh:
            self.on_refresh(self.tokens)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        if self.tokens.expires_at - time.time() < 60:
            self._refresh()
        return {"Authorization": f"Bearer {self.tokens.access_token}", **(extra or {})}

    # ------------------------------------------------------------------ transporte con reintentos
    def request(
        self, method: str, url: str, *, retry: bool = True, headers: dict[str, str] | None = None, **kw: Any
    ) -> httpx.Response:
        attempt = 0
        refreshed = False
        while True:
            attempt += 1
            try:
                resp = self.http.request(method, url, headers=self._headers(headers), **kw)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if not retry or attempt >= self.max_retries:
                    raise DriveError(f"Error de red con Google Drive: {type(exc).__name__}", retryable=True) from exc
                self._backoff(attempt, None)
                continue
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                self._refresh()
                continue
            if resp.status_code == 401:
                raise DriveAuthError("Google rechazó el token (401); vuelva a conectar Google Drive", 401)
            if resp.status_code == 403 and _is_rate_limit(resp):
                if not retry or attempt >= self.max_retries:
                    raise DriveError("Límite de uso de Google Drive alcanzado", 403, retryable=True)
                self._backoff(attempt, resp)
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                if not retry or attempt >= self.max_retries:
                    raise DriveError(f"Google Drive respondió {resp.status_code}", resp.status_code, retryable=True)
                self._backoff(attempt, resp)
                continue
            if resp.status_code >= 400:
                raise DriveError(_error_message(resp), resp.status_code)
            return resp

    def _backoff(self, attempt: int, resp: httpx.Response | None) -> None:
        delay = min(30.0, 0.5 * 2 ** (attempt - 1))
        if resp is not None and resp.headers.get("retry-after", "").isdigit():
            delay = min(60.0, float(resp.headers["retry-after"]))
        log.warning("Reintentando petición a Drive (intento %s) en %.1fs", attempt, delay)
        SLEEP(delay)

    # ------------------------------------------------------------------ operaciones
    def about(self) -> dict:
        return self.request("GET", f"{self.api}/about", params={"fields": "user(displayName,emailAddress)"}).json()

    def get(self, file_id: str) -> dict | None:
        try:
            return self.request("GET", f"{self.api}/files/{file_id}", params={"fields": FILE_FIELDS}).json()
        except DriveError as exc:
            if exc.status == 404:
                return None
            raise

    def list(self, q: str, fields: str = FILE_FIELDS, page_size: int = 100) -> list[dict]:
        out: list[dict] = []
        token = None
        while True:
            params = {"q": q, "fields": f"nextPageToken,files({fields})", "pageSize": page_size, "spaces": "drive"}
            if token:
                params["pageToken"] = token
            data = self.request("GET", f"{self.api}/files", params=params).json()
            out.extend(data.get("files", []))
            token = data.get("nextPageToken")
            if not token:
                return out

    def find_folder(self, name: str, parent: str | None) -> str | None:
        q = f"name = '{_esc(name)}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        q += f" and '{parent}' in parents" if parent else " and 'root' in parents"
        found = self.list(q, fields="id,name")
        return found[0]["id"] if found else None

    def create_folder(self, name: str, parent: str | None) -> str:
        meta: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
        if parent:
            meta["parents"] = [parent]
        return self.request("POST", f"{self.api}/files", params={"fields": "id"}, json=meta).json()["id"]

    def ensure_folder(self, name: str, parent: str | None) -> str:
        return self.find_folder(name, parent) or self.create_folder(name, parent)

    def find_by_property(self, key: str, value: str, parent: str | None = None) -> list[dict]:
        q = f"appProperties has {{ key='{_esc(key)}' and value='{_esc(value)}' }} and trashed = false"
        if parent:
            q += f" and '{parent}' in parents"
        return self.list(q)

    def upload(
        self,
        name: str,
        parent: str,
        mime: str,
        content: bytes,
        app_properties: dict[str, str],
        session_uri: str | None = None,
        on_session: Callable[[str], None] | None = None,
    ) -> dict:
        meta = {"name": name, "parents": [parent], "mimeType": mime, "appProperties": app_properties}
        if len(content) <= RESUMABLE_THRESHOLD and not session_uri:
            return self._multipart("POST", f"{self.upload_api}/files", meta, mime, content)
        return self._resumable("POST", f"{self.upload_api}/files", meta, mime, content, session_uri, on_session)

    def update_content(
        self,
        file_id: str,
        mime: str,
        content: bytes,
        app_properties: dict[str, str] | None = None,
        session_uri: str | None = None,
        on_session: Callable[[str], None] | None = None,
    ) -> dict:
        meta: dict[str, Any] = {}
        if app_properties:
            meta["appProperties"] = app_properties
        url = f"{self.upload_api}/files/{file_id}"
        if len(content) <= RESUMABLE_THRESHOLD and not session_uri:
            return self._multipart("PATCH", url, meta, mime, content)
        return self._resumable("PATCH", url, meta, mime, content, session_uri, on_session)

    def download(self, file_id: str) -> bytes:
        return self.request("GET", f"{self.api}/files/{file_id}", params={"alt": "media"}).content

    def trash(self, file_id: str) -> None:
        self.request("PATCH", f"{self.api}/files/{file_id}", json={"trashed": True})

    def delete(self, file_id: str) -> None:
        self.request("DELETE", f"{self.api}/files/{file_id}")

    def start_page_token(self) -> str:
        return self.request("GET", f"{self.api}/changes/startPageToken").json()["startPageToken"]

    def changes(self, page_token: str) -> tuple[list[dict], str]:
        changes: list[dict] = []
        token = page_token
        while True:
            data = self.request(
                "GET",
                f"{self.api}/changes",
                params={
                    "pageToken": token,
                    "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,file({FILE_FIELDS}))",
                    "pageSize": 200,
                    "spaces": "drive",
                },
            ).json()
            changes.extend(data.get("changes", []))
            if data.get("newStartPageToken"):
                return changes, data["newStartPageToken"]
            token = data["nextPageToken"]

    def permissions(self, file_id: str) -> list[dict]:
        return (
            self.request(
                "GET",
                f"{self.api}/files/{file_id}/permissions",
                params={"fields": "permissions(id,type,role,emailAddress,displayName)"},
            )
            .json()
            .get("permissions", [])
        )

    def share(self, file_id: str, email: str, role: str = "writer") -> dict:
        return self.request(
            "POST",
            f"{self.api}/files/{file_id}/permissions",
            params={"sendNotificationEmail": "false", "fields": "id,type,role,emailAddress"},
            json={"type": "user", "role": role, "emailAddress": email},
        ).json()

    # ------------------------------------------------------------------ subidas
    def _multipart(self, method: str, url: str, meta: dict, mime: str, content: bytes) -> dict:
        boundary = "wadima-" + str(int(time.time() * 1000))
        body = (
            (
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
            ).encode()
            + content
            + f"\r\n--{boundary}--".encode()
        )
        return self.request(
            method,
            url,
            params={"uploadType": "multipart", "fields": FILE_FIELDS},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            content=body,
        ).json()

    def _resumable(
        self,
        method: str,
        url: str,
        meta: dict,
        mime: str,
        content: bytes,
        session_uri: str | None,
        on_session: Callable[[str], None] | None,
    ) -> dict:
        total = len(content)
        offset = 0
        if session_uri:
            offset = self._resume_offset(session_uri, total)
            if offset is None:  # la sesión caducó: empezar de nuevo
                session_uri = None
                offset = 0
            elif isinstance(offset, dict):
                return offset  # ya estaba completa
        if not session_uri:
            resp = self.request(
                method,
                url,
                params={"uploadType": "resumable", "fields": FILE_FIELDS},
                headers={
                    "X-Upload-Content-Type": mime,
                    "X-Upload-Content-Length": str(total),
                    "Content-Type": "application/json; charset=UTF-8",
                },
                content=json.dumps(meta).encode(),
            )
            session_uri = resp.headers["location"]
            if on_session:
                on_session(session_uri)
        while offset < total:
            end = min(offset + CHUNK, total) - 1
            resp = self._put_chunk(session_uri, content, offset, end, total, mime)
            if resp.status_code in (200, 201):
                return resp.json()
            rng = resp.headers.get("range")
            offset = int(rng.split("-")[1]) + 1 if rng else 0
        raise DriveError("La subida reanudable terminó sin respuesta final")

    def _put_chunk(
        self, session_uri: str, content: bytes, offset: int, end: int, total: int, mime: str
    ) -> httpx.Response:
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.http.put(
                    session_uri,
                    content=content[offset : end + 1],
                    headers={"Content-Range": f"bytes {offset}-{end}/{total}", "Content-Type": mime, **self._headers()},
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt == self.max_retries:
                    raise DriveError("Error de red durante la subida reanudable", retryable=True) from exc
                self._backoff(attempt, None)
                current = self._resume_offset(session_uri, total)
                if isinstance(current, dict):
                    return httpx.Response(200, json=current)
                if current is None:
                    raise DriveError("La sesión de subida caducó", retryable=True) from exc
                if current > end:  # el fragmento llegó completo antes del corte
                    return httpx.Response(308, headers={"range": f"bytes=0-{current - 1}"})
                offset = current
                continue
            if resp.status_code in (200, 201, 308):
                return resp
            if resp.status_code in (500, 502, 503, 504, 429) and attempt < self.max_retries:
                self._backoff(attempt, resp)
                continue
            if resp.status_code == 404:
                raise DriveError("La sesión de subida caducó", 404, retryable=True)
            raise DriveError(_error_message(resp), resp.status_code, retryable=resp.status_code >= 500)
        raise DriveError("No se pudo subir el fragmento", retryable=True)

    def _resume_offset(self, session_uri: str, total: int) -> int | dict | None:
        """Consulta cuántos bytes recibió Google de una sesión reanudable (308 + Range)."""
        try:
            resp = self.http.put(session_uri, headers={"Content-Range": f"bytes */{total}", **self._headers()})
        except (httpx.TransportError, httpx.TimeoutException):
            return 0
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code == 308:
            rng = resp.headers.get("range")
            return int(rng.split("-")[1]) + 1 if rng else 0
        return None


def _esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _is_rate_limit(resp: httpx.Response) -> bool:
    try:
        reasons = {e.get("reason") for e in resp.json().get("error", {}).get("errors", [])}
    except ValueError:
        return False
    return bool(reasons & {"rateLimitExceeded", "userRateLimitExceeded"})


def _error_message(resp: httpx.Response) -> str:
    try:
        return f"Google Drive {resp.status_code}: {resp.json()['error']['message']}"
    except (ValueError, KeyError, TypeError):
        return f"Google Drive {resp.status_code}"
