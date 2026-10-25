"""Opt-in, loopback-only local model registry and Ollama preloader.

This is not a public model control plane, deployment scheduler, or an
authorization boundary for the independent Go inference gateway.
"""
from __future__ import annotations

from contextlib import closing
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
from app.local_monitoring import GatewayMonitoring
from app.local_deployments import DeploymentChoice, DeploymentCreate, LocalDeploymentStore, clean_name

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
        with closing(self._connect()) as conn, conn:
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
        with closing(self._connect()) as conn, conn:
            items = conn.execute("SELECT * FROM local_models ORDER BY tag LIMIT ?", (MAX_REGISTRY,)).fetchall()
        return [self._row(item) for item in items]

    def get(self, tag: str) -> dict | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM local_models WHERE tag = ?", (tag,)).fetchone()
        return self._row(row) if row else None

    def add(self, tag: str, digest: str) -> dict:
        with closing(self._connect()) as conn, conn:
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
        with closing(self._connect()) as conn, conn:
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
        except HTTPError as exc:
            # A rejected Ollama request still owns an HTTP response/socket.
            # Never inspect the provider's error body, which may contain
            # private model details; explicitly release it.
            try:
                raise HTTPException(503, "local Ollama model API unavailable or invalid") from None
            finally:
                exc.close()
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            raise HTTPException(503, "local Ollama model API unavailable or invalid") from None

    def _models(self, path: str) -> list[dict]:
        obj = self._request(path)
        raw = obj.get("models")
        if not isinstance(raw, list) or len(raw) > 256:
            raise HTTPException(503, "local Ollama model listing is invalid")
        models = []
        seen = set()
        for item in raw:
            if not isinstance(item, dict):
                raise HTTPException(503, "local Ollama model listing is invalid")
            name, model = item.get("name"), item.get("model")
            # Ollama can report both aliases for one revision. A contradictory
            # response must never be silently resolved by list/field order.
            if (name is not None and model is not None and name != model):
                raise HTTPException(503, "local Ollama model identity is ambiguous")
            tag = name or model
            if not isinstance(tag, str) or not isinstance(item.get("digest"), str):
                raise HTTPException(503, "local Ollama model identity is missing")
            if MODEL_TAG.fullmatch(tag) and DIGEST.fullmatch(item["digest"]):
                # Admission/approval/rollback checks take one model per tag.
                # A duplicate (even same digest) would make the identity
                # dependent on response ordering; reject the whole snapshot.
                if tag in seen:
                    raise HTTPException(503, "local Ollama model identity is ambiguous")
                seen.add(tag)
                models.append({"tag": tag, "digest": item["digest"]})
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
                       gateway_chat: LocalGatewayChat | None = None,
                       gateway_monitoring: GatewayMonitoring | None = None) -> APIRouter:
    chat_client = gateway_chat or LocalGatewayChat()
    monitoring = gateway_monitoring or GatewayMonitoring()
    deployments = LocalDeploymentStore(store.path)
    def operator(request: Request) -> None:
        if request.client is None or request.client.host not in LOOPBACK_CLIENTS:
            raise HTTPException(403, "local operator routes require loopback")
        # Reject ambiguous duplicate Authorization fields. Reverse proxies
        # and the application must agree on exactly one credential.
        values = request.headers.getlist("authorization")
        if (len(values) != 1 or len(values[0]) != len(token) + 7
                or not values[0].startswith("Bearer ")
                or not secrets.compare_digest(values[0][7:], token)):
            raise HTTPException(401, "local operator token required", headers={"WWW-Authenticate": "Bearer"})

    router = APIRouter(prefix="/api/v1/local", tags=["local-models"], dependencies=[Depends(operator)])

    @router.get("/monitoring")
    def local_inference_monitoring() -> dict:
        """Authorized, fresh read-only gateway snapshot. No prompt storage."""
        return monitoring.snapshot()

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

    def require_ready(tag: str, digest: str) -> None:
        record = store.get(_tag(tag))
        if (record is None or not record["approved"] or
                record["digest"] != digest):
            raise HTTPException(409, "deployment model is not approved at the pinned digest")
        _match_installed(runtime, record)
        loaded = next((item for item in runtime.loaded() if item["tag"] == tag), None)
        if loaded is None or loaded["digest"] != digest:
            raise HTTPException(409, "deployment model revision must be loaded first")

    @router.get("/deployments")
    def list_local_deployments() -> dict:
        items = deployments.list()
        if not items:
            return {"deployments": []}
        # One installed + loaded snapshot rather than N model API calls.
        installed = {m["tag"]: m["digest"] for m in runtime.installed()}
        loaded = {m["tag"]: m["digest"] for m in runtime.loaded()}
        registered = {m["tag"]: m for m in store.list()}
        result = []
        for record in items:
            tag, digest = record["active_tag"], record["active_digest"]
            approved = registered.get(tag)
            ready = (record["enabled"] and approved is not None and
                     approved["approved"] and approved["digest"] == digest and
                     installed.get(tag) == digest and loaded.get(tag) == digest)
            result.append({**record, "ready": bool(ready)})
        return {"deployments": result}

    @router.post("/deployments", status_code=201)
    def create_deployment(payload: DeploymentCreate) -> dict:
        name = clean_name(payload.name)
        tag = _tag(payload.tag)
        registered = store.get(tag)
        if registered is None:
            raise HTTPException(404, "model must be registered first")
        require_ready(tag, registered["digest"])
        return deployments.create(name, tag, registered["digest"])

    @router.post("/deployments/{name}/promote")
    def promote_deployment(name: str, payload: DeploymentChoice) -> dict:
        name = clean_name(name)
        original = deployments.get(name)
        if original is None:
            raise HTTPException(404, "local deployment not found")
        tag = _tag(payload.tag)
        registered = store.get(tag)
        if registered is None:
            raise HTTPException(404, "candidate model must be registered")
        require_ready(tag, registered["digest"])
        return deployments.promote(name, tag, registered["digest"],
                                   expected_current=original)

    @router.post("/deployments/{name}/rollback")
    def rollback_deployment(name: str) -> dict:
        name = clean_name(name)
        record = deployments.get(name)
        if record is None:
            raise HTTPException(404, "local deployment not found")
        if record["previous_tag"] is None:
            raise HTTPException(409, "deployment has no previous revision")
        require_ready(record["previous_tag"], record["previous_digest"])
        return deployments.rollback(name, expected_previous_tag=record["previous_tag"],
                                    expected_previous_digest=record["previous_digest"])

    @router.post("/deployments/{name}/stop")
    def stop_deployment(name: str) -> dict:
        # Disables traffic via this stable alias; DOES NOT unload globally
        # shared Ollama weights or change other deployments.
        return deployments.set_enabled(clean_name(name), False)

    @router.post("/deployments/{name}/start")
    def start_deployment(name: str) -> dict:
        record = deployments.get(clean_name(name))
        if record is None:
            raise HTTPException(404, "local deployment not found")
        require_ready(record["active_tag"], record["active_digest"])
        return deployments.set_enabled(name, True)

    @router.post("/deployments/{name}/chat")
    def chat_deployment(name: str, payload: ChatInput) -> dict:
        name = clean_name(name)
        record = deployments.get(name)
        if record is None:
            raise HTTPException(404, "local deployment not found")
        if not record["enabled"]:
            raise HTTPException(409, "local deployment is stopped")
        if not payload.prompt.strip():
            raise HTTPException(422, "nonempty prompt required")
        require_ready(record["active_tag"], record["active_digest"])
        response = chat_client.complete(record["active_tag"], payload.prompt,
                                        payload.max_tokens)
        return {"deployment": name, **response}

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
                           gateway_chat: LocalGatewayChat | None = None,
                           gateway_monitoring: GatewayMonitoring | None = None) -> None:
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
    LocalDeploymentStore(path).initialize()
    if gateway_chat is None:
        gateway_chat = LocalGatewayChat(port=selected_local_gateway_port())
    app.include_router(local_model_router(token, store, runtime or OllamaRuntime(),
                                          gateway_chat=gateway_chat,
                                          gateway_monitoring=gateway_monitoring))
    app.include_router(model_console_router())
