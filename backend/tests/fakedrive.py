"""Simulador en memoria del subconjunto de Google OAuth + Drive v3 que usa WADIMA (para pruebas)."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx

FOLDER = "application/vnd.google-apps.folder"


@dataclass
class FFile:
    id: str
    name: str
    mime: str
    parents: list[str]
    content: bytes = b""
    app_properties: dict = field(default_factory=dict)
    trashed: bool = False
    version: int = 1
    modified: float = field(default_factory=time.time)

    def meta(self) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "mimeType": self.mime,
            "parents": self.parents,
            "trashed": self.trashed,
            "version": str(self.version),
            "modifiedTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(self.modified)),
            "appProperties": self.app_properties,
            "size": str(len(self.content)),
        }
        if self.mime != FOLDER:
            data["md5Checksum"] = hashlib.md5(self.content, usedforsecurity=False).hexdigest()
        return data


class FakeDrive:
    def __init__(self) -> None:
        self.files: dict[str, FFile] = {}
        self.ids = itertools.count(1)
        self.changes: list[str] = []
        self.permissions: dict[str, list[dict]] = {}
        self.sessions: dict[str, dict] = {}
        self.failures: list[object] = []
        self.valid_access: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self.revoked: list[str] = []
        self.requests: list[tuple[str, str]] = []
        self.token_counter = itertools.count(1)
        self.email = "central@example.com"

    # --------------------------------------------------------------- utilidades para las pruebas
    def fail_next(self, n: int, status: int | str = 503) -> None:
        self.failures.extend([status] * n)

    def by_ref(self, ref: str) -> list[FFile]:
        return [f for f in self.files.values() if f.app_properties.get("wadimaRef") == ref and not f.trashed]

    def remote_edit(self, file_id: str, content: bytes) -> None:
        f = self.files[file_id]
        f.content = content
        f.version += 1
        f.modified = time.time()
        self.changes.append(file_id)

    def remote_trash(self, file_id: str) -> None:
        self.files[file_id].trashed = True
        self.changes.append(file_id)

    def folder_named(self, name: str) -> FFile | None:
        return next((f for f in self.files.values() if f.mime == FOLDER and f.name == name and not f.trashed), None)

    def expire_access_tokens(self) -> None:
        self.valid_access.clear()

    # --------------------------------------------------------------- transporte
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        self.requests.append((request.method, url.path))
        if self.failures:
            failure = self.failures.pop(0)
            if failure == "network":
                raise httpx.ConnectError("conexión simulada caída", request=request)
            return httpx.Response(int(failure), json={"error": {"message": "fallo simulado"}})
        if url.netloc.startswith("oauth2"):
            return self._oauth(request, url.path)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.valid_access:
            return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if path.startswith("/upload/drive/v3/files"):
            return self._upload(request, path, q)
        if path == "/drive/v3/about":
            return httpx.Response(200, json={"user": {"displayName": "Cuenta central", "emailAddress": self.email}})
        if path == "/drive/v3/changes/startPageToken":
            return httpx.Response(200, json={"startPageToken": str(len(self.changes))})
        if path == "/drive/v3/changes":
            start = int(q["pageToken"])
            ids = self.changes[start:]
            changes = [{"fileId": i, "removed": False, "file": self.files[i].meta()} for i in ids if i in self.files]
            return httpx.Response(200, json={"changes": changes, "newStartPageToken": str(len(self.changes))})
        m = re.fullmatch(r"/drive/v3/files/([^/]+)/permissions", path)
        if m:
            fid = m.group(1)
            if request.method == "POST":
                body = json.loads(request.content)
                perm = {
                    "id": f"p{len(self.permissions.get(fid, [])) + 1}",
                    "type": "user",
                    "role": body["role"],
                    "emailAddress": body["emailAddress"],
                }
                self.permissions.setdefault(fid, []).append(perm)
                return httpx.Response(200, json=perm)
            owner = {"id": "owner", "type": "user", "role": "owner", "emailAddress": self.email}
            return httpx.Response(200, json={"permissions": [owner, *self.permissions.get(fid, [])]})
        m = re.fullmatch(r"/drive/v3/files/([^/]+)", path)
        if m:
            f = self.files.get(m.group(1))
            if f is None:
                return httpx.Response(404, json={"error": {"message": "File not found"}})
            if request.method == "GET":
                if q.get("alt") == "media":
                    return httpx.Response(200, content=f.content)
                return httpx.Response(200, json=f.meta())
            if request.method == "PATCH":
                body = json.loads(request.content or b"{}")
                if "trashed" in body:
                    f.trashed = body["trashed"]
                    self.changes.append(f.id)
                return httpx.Response(200, json=f.meta())
            if request.method == "DELETE":
                del self.files[f.id]
                return httpx.Response(204)
        if path == "/drive/v3/files" and request.method == "POST":
            body = json.loads(request.content)
            f = self._new(
                body["name"],
                body.get("mimeType", "application/octet-stream"),
                body.get("parents") or ["root"],
                b"",
                body.get("appProperties") or {},
            )
            return httpx.Response(200, json={"id": f.id})
        if path == "/drive/v3/files" and request.method == "GET":
            return httpx.Response(200, json={"files": [f.meta() for f in self._query(q.get("q", ""))]})
        return httpx.Response(404, json={"error": {"message": f"ruta no simulada {path}"}})

    # --------------------------------------------------------------- internos
    def _new(self, name, mime, parents, content, props) -> FFile:
        f = FFile(
            id=f"f{next(self.ids)}", name=name, mime=mime, parents=parents, content=content, app_properties=dict(props)
        )
        self.files[f.id] = f
        self.changes.append(f.id)
        return f

    def _query(self, q: str) -> list[FFile]:
        out = []
        for f in self.files.values():
            ok = True
            for clause in re.split(r"\s+and\s+(?![^{]*})", q):
                clause = clause.strip()
                if not clause:
                    continue
                if m := re.fullmatch(r"name = '(.*)'", clause):
                    ok &= f.name == m.group(1).replace("\\'", "'")
                elif m := re.fullmatch(r"mimeType = '(.*)'", clause):
                    ok &= f.mime == m.group(1)
                elif clause == "trashed = false":
                    ok &= not f.trashed
                elif m := re.fullmatch(r"'(.*)' in parents", clause):
                    ok &= m.group(1) in f.parents
                elif m := re.fullmatch(r"appProperties has \{ key='(.*)' and value='(.*)' \}", clause):
                    ok &= f.app_properties.get(m.group(1)) == m.group(2)
                else:
                    raise AssertionError(f"Cláusula no soportada por el simulador: {clause}")
            if ok:
                out.append(f)
        return out

    def _parse_multipart(self, request: httpx.Request) -> tuple[dict, bytes]:
        boundary = request.headers["content-type"].split("boundary=")[1].encode()
        parts = request.content.split(b"--" + boundary)
        meta_part, media_part = parts[1], parts[2]
        meta = json.loads(meta_part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0])
        media = media_part.split(b"\r\n\r\n", 1)[1]
        if media.endswith(b"\r\n"):
            media = media[:-2]
        return meta, media

    def _upload(self, request: httpx.Request, path: str, q: dict) -> httpx.Response:
        if request.method == "PUT":  # fragmento de subida reanudable
            sid = q["upload_id"]
            sess = self.sessions.get(sid)
            if sess is None:
                return httpx.Response(404, json={"error": {"message": "session expired"}})
            rng = request.headers["content-range"]
            if rng.startswith("bytes */"):
                if sess.get("done"):
                    return httpx.Response(200, json=self.files[sess["done"]].meta())
                received = len(sess["data"])
                headers = {"range": f"bytes=0-{received - 1}"} if received else {}
                return httpx.Response(308, headers=headers)
            m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", rng)
            start, end, total = map(int, m.groups())
            if start != len(sess["data"]):
                return httpx.Response(400, json={"error": {"message": "offset incorrecto"}})
            sess["data"] += request.content
            if len(sess["data"]) < total:
                return httpx.Response(308, headers={"range": f"bytes=0-{len(sess['data']) - 1}"})
            return self._finish_session(sid, sess)
        m = re.fullmatch(r"/upload/drive/v3/files(?:/([^/]+))?", path)
        file_id = m.group(1)
        if q.get("uploadType") == "multipart":
            meta, media = self._parse_multipart(request)
            if file_id:
                f = self.files[file_id]
                f.content = media
                f.version += 1
                f.modified = time.time()
                f.app_properties.update(meta.get("appProperties") or {})
                self.changes.append(f.id)
            else:
                f = self._new(
                    meta["name"],
                    meta.get("mimeType", "application/octet-stream"),
                    meta["parents"],
                    media,
                    meta.get("appProperties") or {},
                )
            return httpx.Response(200, json=f.meta())
        if q.get("uploadType") == "resumable":
            sid = f"s{len(self.sessions) + 1}"
            self.sessions[sid] = {"meta": json.loads(request.content or b"{}"), "file_id": file_id, "data": b""}
            return httpx.Response(
                200, headers={"location": f"https://www.googleapis.com/upload/drive/v3/files?upload_id={sid}"}
            )
        return httpx.Response(400, json={"error": {"message": "uploadType no soportado"}})

    def _finish_session(self, sid: str, sess: dict) -> httpx.Response:
        meta = sess["meta"]
        if sess["file_id"]:
            f = self.files[sess["file_id"]]
            f.content = sess["data"]
            f.version += 1
            f.modified = time.time()
            self.changes.append(f.id)
        else:
            f = self._new(
                meta["name"],
                meta.get("mimeType", "application/octet-stream"),
                meta["parents"],
                sess["data"],
                meta.get("appProperties") or {},
            )
        sess["done"] = f.id
        return httpx.Response(200, json=f.meta())

    def _oauth(self, request: httpx.Request, path: str) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if path == "/revoke":
            self.revoked.append(form["token"])
            self.refresh_tokens.discard(form["token"])
            return httpx.Response(200)
        if path == "/token":
            if form["grant_type"] == "authorization_code":
                if form.get("code") != "good-code" or not form.get("code_verifier"):
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad code"})
                refresh = f"1//refresh-{next(self.token_counter)}"
                self.refresh_tokens.add(refresh)
            elif form["grant_type"] == "refresh_token":
                if form["refresh_token"] not in self.refresh_tokens:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "revoked"})
                refresh = None
            else:
                return httpx.Response(400, json={"error": "unsupported_grant_type"})
            access = f"ya29.access-{next(self.token_counter)}"
            self.valid_access.add(access)
            body = {
                "access_token": access,
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/drive.file",
            }
            if refresh:
                body["refresh_token"] = refresh
            return httpx.Response(200, json=body)
        return httpx.Response(404)
