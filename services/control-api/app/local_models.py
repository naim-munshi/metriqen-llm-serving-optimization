"""Opt-in, loopback-only local model registry and Ollama preloader.

This is not a public model control plane, deployment scheduler, or an
authorization boundary for the independent Go inference gateway.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request as HTTPRequest, build_opener

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from app.local_inference import ChatInput, LocalGatewayChat, selected_local_gateway_port

MODEL_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}:[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
LOOPBACK_CLIENTS = frozenset(("127.0.0.1", "::1"))
MAX_REGISTRY = 100


class ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tag: str


def _tag(tag: str) -> str:
    if not isinstance(tag, str) or MODEL_TAG.fullmatch(tag) is None:
        raise HTTPException(422, "a canonical model:tag identifier is required")
    return tag


def _digest(digest: object) -> str:
    if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
        raise ValueError("Ollama did not provide a valid model digest")
    return digest


class Registry:
    """Local SQLite persistence; no tenant isolation or production migration."""

    def __init__(self, path: Path):
        if not path.is_absolute() or path.suffix != ".sqlite3":
            raise ValueError("local model registry database must be an absolute .sqlite3 path")
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS local_models (
                tag TEXT PRIMARY KEY,
                digest TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)),
                registered_at_utc TEXT NOT NULL
            )""")
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        return {"tag": row["tag"], "digest": row["digest"],
                "approved": bool(row["approved"]),
                "registered_at_utc": row["registered_at_utc"]}

    def list(self) -> list[dict]:
        with self._connect() as conn:
            items = conn.execute("SELECT * FROM local_models ORDER BY tag LIMIT ?", (MAX_REGISTRY,)).fetchall()
        return [self._row(item) for item in items]

    def get(self, tag: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM local_models WHERE tag = ?", (tag,)).fetchone()
        return self._row(row) if row else None

    def add(self, tag: str, digest: str) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute("SELECT count(*) FROM local_models").fetchone()[0]
            if count >= MAX_REGISTRY:
                raise HTTPException(409, "local model registry is full")
            try:
                conn.execute("INSERT INTO local_models(tag,digest,approved,registered_at_utc) VALUES(?,?,0,?)",
                             (tag, digest, datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "model tag already registered") from None
        value = self.get(tag)
        if value is None:
            raise RuntimeError("model insert not found")
        return value

    def approve(self, tag: str) -> dict:
        with self._connect() as conn:
            conn.execute("UPDATE local_models SET approved = 1 WHERE tag = ?", (tag,))
        record = self.get(tag)
        if record is None:
            raise HTTPException(404, "model not registered")
        return record


class LocalModelRuntime(Protocol):
    def installed(self) -> list[dict]: ...
    def loaded(self) -> list[dict]: ...
    def preload(self, tag: str) -> None: ...
    def unload(self, tag: str) -> None: ...


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OllamaRuntime:
    """Fixed-origin native Ollama client, no caller URL, redirects or proxies."""

    ORIGIN = "http://127.0.0.1:11434"

    def __init__(self) -> None:
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _request(self, path: str, *, payload: dict | None = None, timeout: int = 4) -> dict:
        if path not in ("/api/tags", "/api/ps", "/api/generate"):
            raise ValueError("unsupported Ollama API path")
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        req = HTTPRequest(self.ORIGIN + path, data=data,
                          headers={"Accept": "application/json", "Content-Type": "application/json"},
                          method="POST" if data is not None else "GET")
        try:
            with self.opener.open(req, timeout=timeout) as response:
                if response.headers.get_content_type() != "application/json":
                    raise ValueError("Ollama response content type invalid")
                raw = response.read(262145)
                if len(raw) > 262144:
                    raise ValueError("Ollama response too large")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("Ollama response is not an object")
                return value
        except (OSError, URLError, HTTPError, ValueError, json.JSONDecodeError):
            raise HTTPException(503, "local Ollama model API unavailable or invalid") from None

    def _models(self, path: str) -> list[dict]:
        obj = self._request(path)
        raw = obj.get("models")
        if not isinstance(raw, list) or len(raw) > 256:
            raise HTTPException(503, "local Ollama model listing is invalid")
        models = []
        for item in raw:
            if not isinstance(item, dict):
                raise HTTPException(503, "local Ollama model listing is invalid")
            name = item.get("name") or item.get("model")
            if not isinstance(name, str) or not isinstance(item.get("digest"), str):
                raise HTTPException(503, "local Ollama model identity is missing")
            if MODEL_TAG.fullmatch(name) and DIGEST.fullmatch(item["digest"]):
                models.append({"tag": name, "digest": item["digest"]})
        return models

    def installed(self) -> list[dict]:
        return self._models("/api/tags")

    def loaded(self) -> list[dict]:
        return self._models("/api/ps")

    def preload(self, tag: str) -> None:
        result = self._request("/api/generate",
                               payload={"model": tag, "keep_alive": "5m", "stream": False},
                               timeout=120)
        if result.get("done") is not True:
            raise HTTPException(503, "Ollama did not confirm model load")

    def unload(self, tag: str) -> None:
        result = self._request("/api/generate",
                               payload={"model": tag, "keep_alive": 0, "stream": False},
                               timeout=30)
        if result.get("done") is not True:
            raise HTTPException(503, "Ollama did not confirm model unload")


def _match_installed(runtime: LocalModelRuntime, record: dict) -> None:
    for available in runtime.installed():
        if available["tag"] == record["tag"]:
            if available["digest"] != record["digest"]:
                raise HTTPException(409, "installed model revision differs from registered digest")
            return
    raise HTTPException(409, "registered model is no longer installed")


def local_model_router(token: str, store: Registry, runtime: LocalModelRuntime,
                       gateway_chat: LocalGatewayChat | None = None) -> APIRouter:
    chat_client = gateway_chat or LocalGatewayChat()
    def operator(request: Request) -> None:
        if request.client is None or request.client.host not in LOOPBACK_CLIENTS:
            raise HTTPException(403, "local operator routes require loopback")
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or not secrets.compare_digest(authorization[7:], token):
            raise HTTPException(401, "local operator token required", headers={"WWW-Authenticate": "Bearer"})

    router = APIRouter(prefix="/api/v1/local", tags=["local-models"], dependencies=[Depends(operator)])

    @router.get("/available-models")
    def available_models() -> dict:
        return {"models": runtime.installed()}

    @router.get("/models")
    def models() -> dict:
        return {"models": store.list()}

    @router.get("/model-statuses")
    def model_statuses() -> dict:
        """One bounded Ollama /api/ps snapshot for the whole local registry."""
        registered = store.list()
        running = runtime.loaded()
        models = []
        for record in registered:
            loaded = next((item for item in running if item["tag"] == record["tag"]), None)
            matches = loaded is None or loaded["digest"] == record["digest"]
            models.append({
                "tag": record["tag"],
                "approved": record["approved"],
                "loaded": loaded is not None and matches,
                "digest_matches_loaded": matches,
            })
        return {"models": models}

    @router.post("/models", status_code=201)
    def register_model(payload: ModelInput) -> dict:
        tag = _tag(payload.tag)
        matching = next((m for m in runtime.installed() if m["tag"] == tag), None)
        if matching is None:
            raise HTTPException(404, "model not installed in local Ollama")
        return store.add(tag, _digest(matching["digest"]))

    @router.post("/models/{tag}/approve")
    def approve_model(tag: str) -> dict:
        registered = store.get(_tag(tag))
        if registered is None:
            raise HTTPException(404, "model not registered")
        _match_installed(runtime, registered)
        return store.approve(tag)

    @router.get("/models/{tag}/status")
    def model_status(tag: str) -> dict:
        registered = store.get(_tag(tag))
        if registered is None:
            raise HTTPException(404, "model not registered")
        loaded = next((m for m in runtime.loaded() if m["tag"] == tag), None)
        return {"tag": tag, "approved": registered["approved"],
                "loaded": loaded is not None and loaded["digest"] == registered["digest"],
                "digest_matches_loaded": loaded is None or loaded["digest"] == registered["digest"]}

    @router.post("/models/{tag}/load")
    def load_model(tag: str) -> dict:
        registered = store.get(_tag(tag))
        if registered is None:
            raise HTTPException(404, "model not registered")
        if not registered["approved"]:
            raise HTTPException(403, "model must be approved before loading")
        _match_installed(runtime, registered)
        runtime.preload(tag)
        found = next((m for m in runtime.loaded() if m["tag"] == tag), None)
        if found is None or found["digest"] != registered["digest"]:
            raise HTTPException(409, "loaded model digest not verified")
        return {"tag": tag, "digest": registered["digest"], "loaded": True,
                "note": "Ollama preload only; not a managed serving deployment"}

    @router.post("/models/{tag}/chat")
    def approved_model_chat(tag: str, payload: ChatInput) -> dict:
        record = store.get(_tag(tag))
        if record is None:
            raise HTTPException(404, "model not registered")
        if not record["approved"]:
            raise HTTPException(403, "model not approved for local inference")
        if not payload.prompt.strip():
            raise HTTPException(422, "nonempty prompt required")
        _match_installed(runtime, record)
        loaded = next((m for m in runtime.loaded() if m["tag"] == tag), None)
        if loaded is None or loaded["digest"] != record["digest"]:
            raise HTTPException(409, "approved model revision is not loaded")
        return chat_client.complete(tag, payload.prompt, payload.max_tokens)

    @router.post("/models/{tag}/unload")
    def unload_model(tag: str) -> dict:
        registered = store.get(_tag(tag))
        if registered is None:
            raise HTTPException(404, "model not registered")
        if not registered["approved"]:
            raise HTTPException(403, "model must be approved before unloading")
        found = next((m for m in runtime.loaded() if m["tag"] == tag), None)
        if found is None:
            return {"tag": tag, "unloaded": True, "already_unloaded": True}
        if found["digest"] != registered["digest"]:
            raise HTTPException(409, "loaded model digest differs from registered revision")
        runtime.unload(tag)
        if next((m for m in runtime.loaded() if m["tag"] == tag), None) is not None:
            raise HTTPException(503, "Ollama still reports model loaded")
        return {"tag": tag, "unloaded": True, "already_unloaded": False}

    return router


def model_console_router() -> APIRouter:
    """Serve static same-origin development UI only when model ops are enabled."""
    assets = Path(__file__).resolve().parent / "model_console"
    router = APIRouter(include_in_schema=False)
    csp = ("default-src 'none'; script-src 'self'; style-src 'self'; "
           "connect-src 'self'; base-uri 'none'; form-action 'none'; "
           "frame-ancestors 'none'")
    def asset_handler(filename: str, media_type: str):
        # Closure defaults must NOT become caller-overridable query parameters.
        def serve_asset() -> Response:
            return Response(
                content=(assets / filename).read_bytes(),
                media_type=media_type,
                headers={
                    "Content-Security-Policy": csp,
                    "Cache-Control": "no-store",
                    "X-Frame-Options": "DENY",
                },
            )
        return serve_asset

    for suffix, filename, media in (
        ("", "index.html", "text/html"),
        ("/app.css", "app.css", "text/css"),
        ("/app.js", "app.js", "application/javascript"),
    ):
        router.add_api_route("/console/models" + suffix, asset_handler(filename, media),
                             methods=["GET"], include_in_schema=False)
    return router


def configure_local_models(app: FastAPI, *, token: str | None = None,
                           path: Path | None = None,
                           runtime: LocalModelRuntime | None = None,
                           gateway_chat: LocalGatewayChat | None = None) -> None:
    """Explicit local opt-in; default control API remains unchanged."""
    if token is None:
        token = os.getenv("METRIQEN_LOCAL_MODEL_TOKEN", "")
    if path is None:
        raw_path = os.getenv("METRIQEN_LOCAL_MODEL_DB", "")
        path = Path(raw_path) if raw_path else None
    if not token and path is None:
        return
    if (not isinstance(token, str) or not 32 <= len(token) <= 256
            or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise ValueError("local model operator token must be 32-256 printable nonspace ASCII bytes")
    if path is None:
        raise ValueError("METRIQEN_LOCAL_MODEL_DB is required with local operator token")
    store = Registry(path)
    store.initialize()
    if gateway_chat is None:
        gateway_chat = LocalGatewayChat(port=selected_local_gateway_port())
    app.include_router(local_model_router(token, store, runtime or OllamaRuntime(),
                                          gateway_chat=gateway_chat))
    app.include_router(model_console_router())
