"""Synthetic local model workflow; no real Ollama required."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.local_models import Registry, OllamaRuntime, configure_local_models

TAG = "llama3.2:1b"
DIGEST = "baf6a787fdffd633537aa2eb51cfd54cb93ff08e28040095462bb63daf552878"
TOKEN = "operator-local-secret-token-with-32-chars"  # test-only


class FakeRuntime:
    def __init__(self):
        self.available = [{"tag": TAG, "digest": DIGEST}]
        self.running = []
        self.calls = 0

    def installed(self):
        return list(self.available)

    def loaded(self):
        return list(self.running)

    def preload(self, tag):
        self.calls += 1
        self.running = [item for item in self.available if item["tag"] == tag]


@pytest.fixture
def sandbox(tmp_path):
    path = tmp_path / "models.sqlite3"
    fake = FakeRuntime()
    app = FastAPI()
    configure_local_models(app, token=TOKEN, path=path, runtime=fake)
    client = TestClient(app, client=("127.0.0.1", 54321))
    return client, {"Authorization": f"Bearer {TOKEN}"}, fake, path


def test_not_enabled_keeps_default_control_api_unchanged(monkeypatch):
    monkeypatch.delenv("METRIQEN_LOCAL_MODEL_TOKEN", raising=False)
    monkeypatch.delenv("METRIQEN_LOCAL_MODEL_DB", raising=False)
    app = FastAPI()
    configure_local_models(app)
    assert TestClient(app).get("/api/v1/local/models").status_code == 404


def test_configuration_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("METRIQEN_LOCAL_MODEL_DB", raising=False)
    for token in ("short", "with whitespace " + "a" * 20, "a" * 32 + "\n", "ব" * 32):
        with pytest.raises(ValueError):
            configure_local_models(FastAPI(), token=token, path=tmp_path / "models.sqlite3")
    with pytest.raises(ValueError):
        configure_local_models(FastAPI(), token=TOKEN, path=Path("relative.sqlite3"))
    with pytest.raises(ValueError):
        configure_local_models(FastAPI(), token=TOKEN, path=None)


def test_unauthenticated_denied_before_any_runtime_calls(sandbox):
    client, headers, fake, _ = sandbox
    for method, uri, body in [
        ("GET", "/api/v1/local/models", None),
        ("GET", "/api/v1/local/available-models", None),
        ("POST", "/api/v1/local/models", {"tag": TAG}),
        ("POST", f"/api/v1/local/models/{TAG}/load", None),
    ]:
        assert client.request(method, uri, json=body).status_code == 401
    assert fake.calls == 0
    assert client.get("/api/v1/local/models", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/api/v1/local/models", headers=headers).status_code == 200


def test_nonloopback_request_denied_even_with_valid_token(sandbox):
    client, headers, fake, _ = sandbox
    remote = TestClient(client.app, client=("192.0.2.50", 54321))
    assert remote.get("/api/v1/local/models", headers=headers).status_code == 403
    assert fake.calls == 0


def test_register_approve_load_and_persist(sandbox):
    client, headers, fake, path = sandbox
    assert client.get("/api/v1/local/available-models", headers=headers).json() == {
        "models": [{"tag": TAG, "digest": DIGEST}]
    }
    recorded = client.post("/api/v1/local/models", json={"tag": TAG}, headers=headers)
    assert recorded.status_code == 201
    assert recorded.json()["digest"] == DIGEST
    assert recorded.json()["approved"] is False
    assert client.post(f"/api/v1/local/models/{TAG}/load", headers=headers).status_code == 403
    assert fake.calls == 0
    assert client.get(f"/api/v1/local/models/{TAG}/status", headers=headers).json()["loaded"] is False
    approved = client.post(f"/api/v1/local/models/{TAG}/approve", headers=headers)
    assert approved.status_code == 200 and approved.json()["approved"] is True
    loaded = client.post(f"/api/v1/local/models/{TAG}/load", headers=headers)
    assert loaded.status_code == 200
    assert loaded.json()["loaded"] is True
    assert "not a managed" in loaded.json()["note"]
    assert fake.calls == 1
    assert client.get(f"/api/v1/local/models/{TAG}/status", headers=headers).json()["loaded"] is True
    assert path.exists()
    assert Registry(path).get(TAG)["approved"] is True
    assert "Bearer" not in str(Registry(path).list())


def test_registry_never_silently_replaces_digest(sandbox):
    client, headers, fake, path = sandbox
    assert client.post("/api/v1/local/models", json={"tag": TAG}, headers=headers).status_code == 201
    assert client.post("/api/v1/local/models", json={"tag": TAG}, headers=headers).status_code == 409
    fake.available = [{"tag": TAG, "digest": "f" * 64}]
    assert client.post(f"/api/v1/local/models/{TAG}/approve", headers=headers).status_code == 409
    assert client.post(f"/api/v1/local/models/{TAG}/load", headers=headers).status_code == 403
    assert Registry(path).get(TAG)["digest"] == DIGEST


def test_loaded_revision_mismatch_never_claims_loaded(sandbox):
    client, headers, fake, _ = sandbox
    assert client.post("/api/v1/local/models", json={"tag": TAG}, headers=headers).status_code == 201
    assert client.post(f"/api/v1/local/models/{TAG}/approve", headers=headers).status_code == 200
    fake.running = [{"tag": TAG, "digest": "f" * 64}]
    status = client.get(f"/api/v1/local/models/{TAG}/status", headers=headers).json()
    assert status["loaded"] is False and status["digest_matches_loaded"] is False
    fake.available = [{"tag": TAG, "digest": "f" * 64}]
    assert client.post(f"/api/v1/local/models/{TAG}/load", headers=headers).status_code == 409
    assert fake.calls == 0


def test_payload_and_tag_validation(sandbox):
    client, headers, fake, _ = sandbox
    for payload in ({"tag": "../../evil"}, {"tag": TAG, "digest": "f" * 64}, {"tag": 123}, {}):
        assert client.post("/api/v1/local/models", json=payload, headers=headers).status_code == 422
    assert client.post("/api/v1/local/models", json={"tag": "unknown:1"}, headers=headers).status_code == 404
    assert client.post("/api/v1/local/models/not-a-model/load", headers=headers).status_code == 422
    assert client.get("/api/v1/local/models", headers=headers).json() == {"models": []}


def test_unregistered_cannot_approve_or_load(sandbox):
    client, headers, fake, _ = sandbox
    assert client.post(f"/api/v1/local/models/{TAG}/approve", headers=headers).status_code == 404
    assert client.post(f"/api/v1/local/models/{TAG}/load", headers=headers).status_code == 404
    assert fake.calls == 0


def test_native_ollama_http_adapter_uses_only_fixed_local_api():
    class NativeFake(BaseHTTPRequestHandler):
        loaded = []
        path_history = []

        def log_message(self, *_):
            pass

        def respond(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            type(self).path_history.append(self.path)
            body = {"models": [{"name": TAG, "digest": DIGEST}]} if self.path == "/api/tags" else {
                "models": list(type(self).loaded)}
            self.respond(body)

        def do_POST(self):
            type(self).path_history.append(self.path)
            assert self.path == "/api/generate"
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            assert data == {"model": TAG, "keep_alive": "5m", "stream": False}
            type(self).loaded = [{"name": TAG, "digest": DIGEST}]
            self.respond({"done": True, "response": ""})

    server = ThreadingHTTPServer(("127.0.0.1", 0), NativeFake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        runtime = OllamaRuntime()
        runtime.ORIGIN = f"http://127.0.0.1:{server.server_port}"  # test-only fixed local fixture
        assert runtime.installed() == [{"tag": TAG, "digest": DIGEST}]
        runtime.preload(TAG)
        assert runtime.loaded() == [{"tag": TAG, "digest": DIGEST}]
        assert NativeFake.path_history == ["/api/tags", "/api/generate", "/api/ps"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_control_app_opt_in_registers_routes(monkeypatch, tmp_path):
    from app.main import create_app
    monkeypatch.setenv("METRIQEN_LOCAL_MODEL_TOKEN", TOKEN)
    monkeypatch.setenv("METRIQEN_LOCAL_MODEL_DB", str(tmp_path / "models.sqlite3"))
    app = create_app()
    client = TestClient(app, client=("127.0.0.1", 51111))
    assert client.get("/api/v1/local/models").status_code == 401
    response = client.get("/api/v1/local/models",
                          headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json() == {"models": []}
    assert client.get("/health/live").json()["status"] == "ok"
