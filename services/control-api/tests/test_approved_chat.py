"""Contract tests for the local approved-model-to-Go-gateway inference seam."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.local_inference import LocalGatewayChat
from app.local_models import configure_local_models

TAG = "llama3.2:1b"
DIGEST = "a" * 64
SECRET = "synthetic-operator-chat-token-32-characters"


class Runtime:
    def __init__(self):
        self.installed_models = [{"tag": TAG, "digest": DIGEST}]
        self.loaded_models = []

    def installed(self):
        return list(self.installed_models)

    def loaded(self):
        return list(self.loaded_models)

    def preload(self, tag):
        self.loaded_models = list(self.installed_models)

    def unload(self, tag):
        self.loaded_models = []


class Gateway:
    def __init__(self):
        self.calls = []

    def complete(self, tag, prompt, max_tokens):
        self.calls.append((tag, prompt, max_tokens))
        return {"tag": tag, "content": "METRIQEN_READY", "finish_reason": "stop"}


@pytest.fixture
def client(tmp_path):
    runtime = Runtime()
    gateway = Gateway()
    app = FastAPI()
    configure_local_models(app, token=SECRET, path=tmp_path / "registry.sqlite3",
                           runtime=runtime, gateway_chat=gateway)
    return TestClient(app, client=("127.0.0.1", 55332)), runtime, gateway


def post(client, uri, payload=None, *, auth=True):
    headers = {"Authorization": "Bearer " + SECRET} if auth else {}
    return client.post(uri, json=payload, headers=headers)


def setup_model(client):
    assert post(client, "/api/v1/local/models", {"tag": TAG}).status_code == 201
    assert post(client, f"/api/v1/local/models/{TAG}/approve").status_code == 200


def test_local_inference_requires_registered_approved_loaded_revision(client):
    http, runtime, gateway = client
    uri = f"/api/v1/local/models/{TAG}/chat"
    assert post(http, uri, {"prompt": "hello"}, auth=False).status_code == 401
    assert post(http, uri, {"prompt": "hello"}).status_code == 404
    assert post(http, "/api/v1/local/models", {"tag": TAG}).status_code == 201
    assert post(http, uri, {"prompt": "hello"}).status_code == 403
    assert post(http, f"/api/v1/local/models/{TAG}/approve").status_code == 200
    assert post(http, uri, {"prompt": "hello"}).status_code == 409
    runtime.loaded_models = [{"tag": TAG, "digest": "b" * 64}]
    assert post(http, uri, {"prompt": "hello"}).status_code == 409
    runtime.loaded_models = [{"tag": TAG, "digest": DIGEST}]
    good = post(http, uri, {"prompt": "hello", "max_tokens": 12})
    assert good.status_code == 200
    assert good.json() == {"tag": TAG, "content": "METRIQEN_READY", "finish_reason": "stop"}
    assert gateway.calls == [(TAG, "hello", 12)]


def test_model_tag_revision_change_blocks_inference(client):
    http, runtime, gateway = client
    setup_model(http)
    runtime.loaded_models = [{"tag": TAG, "digest": DIGEST}]
    runtime.installed_models = [{"tag": TAG, "digest": "b" * 64}]
    assert post(http, f"/api/v1/local/models/{TAG}/chat", {"prompt": "x"}).status_code == 409
    assert gateway.calls == []


def test_chat_payload_is_strict_and_bounded(client):
    http, runtime, gateway = client
    setup_model(http)
    runtime.loaded_models = [{"tag": TAG, "digest": DIGEST}]
    uri = f"/api/v1/local/models/{TAG}/chat"
    for body in [
        {}, {"prompt": ""}, {"prompt": "  "}, {"prompt": "X" * 4097},
        {"prompt": "hi", "max_tokens": 0}, {"prompt": "hi", "max_tokens": 300},
        {"prompt": "hi", "max_tokens": 3.1}, {"prompt": "hi", "model": "evil:latest"},
    ]:
        assert post(http, uri, body).status_code == 422
    assert gateway.calls == []


def test_local_gateway_client_forwards_only_bounded_contract():
    seen = []

    class GatewayFake(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            assert self.headers.get("Authorization") is None
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(payload)
            body = json.dumps({"model": TAG,
                               "choices": [{"message": {"role": "assistant", "content": "RESULT"},
                                            "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayFake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        gateway = LocalGatewayChat()
        gateway.ORIGIN = f"http://127.0.0.1:{server.server_port}"  # synthetic loopback fixture
        response = gateway.complete(TAG, "Explain TTFT", 24)
        assert response == {"tag": TAG, "content": "RESULT", "finish_reason": "stop"}
        assert seen == [{
            "model": TAG, "messages": [{"role": "user", "content": "Explain TTFT"}],
            "temperature": 0, "max_tokens": 24, "stream": False,
        }]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_gateway_unavailable_and_malformed_never_claim_success():
    client = LocalGatewayChat()
    client.ORIGIN = "http://127.0.0.1:1"
    with pytest.raises(HTTPException) as error:
        client.complete(TAG, "hello", 12)
    assert error.value.status_code == 503
    assert "hello" not in str(error.value.detail)
