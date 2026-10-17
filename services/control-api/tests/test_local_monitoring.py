"""Local monitoring integration tests using a real loopback synthetic Go metrics server."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.local_models import configure_local_models
from app.local_monitoring import GatewayMonitoring

TOKEN = "synthetic-local-monitoring-token-32characters"
SAMPLES = {
    "/metrics/inference": {
        "schema_version": 3,
        "attempts_total": 6,
        "active_attempts": 1,
        "nonstream_complete_total": 3,
        "stream_clean_eof_total": 1,
        "stream_done_total": 1,
        "stream_missing_done_total": 0,
        "pre_response_failure_total": 1,
        "post_response_interrupted_total": 0,
        "client_canceled_total": 0,
        "duration_observations_total": 5,
        "duration_total_us": 12500000,
    },
    "/metrics/admission": {
        "schema_version": 2,
        "active_requests": 1,
        "waiting_requests": 0,
        "max_active_requests": 4,
        "max_waiting_requests": 2,
        "admitted_immediate_total": 4,
        "admitted_queued_total": 1,
        "rejected_full_total": 2,
        "queue_timeout_total": 0,
        "queue_canceled_total": 0,
        "queue_wait_observations_total": 1,
        "queue_wait_duration_total_us": 10000,
    },
    "/metrics/upstream": {
        "schema_version": 1,
        "connection_refused_total": 1,
        "transport_errors_total": 0,
        "timeouts_total": 0,
        "http_rejected_total": 1,
        "http_other_status_total": 0,
        "protocol_errors_total": 0,
        "response_too_large_total": 0,
        "stream_read_errors_total": 0,
        "stream_size_limit_total": 0,
    },
}


class DummyRuntime:
    def installed(self):
        return []
    def loaded(self):
        return []
    def preload(self, tag):
        raise AssertionError("model operation not used")
    def unload(self, tag):
        raise AssertionError("model operation not used")


@contextmanager
def fake_gateway():
    state = {"paths": [], "data": json.loads(json.dumps(SAMPLES)),
             "content_type": "application/json", "body_override": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            state["paths"].append((self.path, self.headers.get("Authorization")))
            if self.path not in state["data"]:
                self.send_error(404)
                return
            raw = (state["body_override"] if state["body_override"] is not None else
                   json.dumps(state["data"][self.path]).encode())
            self.send_response(200)
            self.send_header("Content-Type", state["content_type"])
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            self.send_error(405)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=4)


@pytest.fixture
def sandbox(tmp_path):
    with fake_gateway() as (origin, state):
        adapter = GatewayMonitoring()
        adapter.ORIGIN = origin  # test-only loopback fixture, never caller configured
        app = FastAPI()
        configure_local_models(app, token=TOKEN, path=tmp_path / "models.sqlite3",
                               runtime=DummyRuntime(), gateway_monitoring=adapter)
        client = TestClient(app, client=("127.0.0.1", 54321))
        yield client, state


def test_metrics_require_local_operator_authentication(sandbox):
    client, state = sandbox
    for header in ({}, {"Authorization": "Bearer wrong"}):
        response = client.get("/api/v1/local/monitoring", headers=header)
        assert response.status_code == 401
    assert state["paths"] == []
    other_host = TestClient(client.app, client=("198.51.100.10", 12345))
    result = other_host.get("/api/v1/local/monitoring",
                            headers={"Authorization": "Bearer " + TOKEN})
    assert result.status_code == 403
    assert state["paths"] == []


def test_metrics_exact_go_contract_and_arithmetic(sandbox):
    client, state = sandbox
    response = client.get("/api/v1/local/monitoring",
                          headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    value = response.json()
    assert value["schema_version"] == 1
    assert value["source"] == "local_gateway_8081"
    assert value["process_local_counters"] is True
    assert value["inference"]["admitted_attempts_total"] == 6
    assert value["inference"]["mean_post_admission_duration_ms"] == 2500
    assert value["inference"]["stream_clean_eof_total"] == 1
    assert value["admission"]["rejected_full_total"] == 2
    assert value["upstream"]["failure_events_total"] == 2
    assert all("p95" not in key and "ttft" not in key for key in value["inference"])
    assert state["paths"] == [
        ("/metrics/inference", None), ("/metrics/admission", None),
        ("/metrics/upstream", None),
    ]


@pytest.mark.parametrize("mutation", [
    ("schema", "/metrics/inference", "schema_version", 999),
    ("bool", "/metrics/inference", "attempts_total", True),
    ("negative", "/metrics/inference", "attempts_total", -1),
    ("missing", "/metrics/upstream", "timeouts_total", None),
    ("inconsistent", "/metrics/inference", "attempts_total", 1),
    ("overcapacity", "/metrics/admission", "active_requests", 100),
])
def test_invalid_metrics_never_return_success(sandbox, mutation):
    client, state = sandbox
    _, path, field, value = mutation
    if mutation[0] == "missing":
        del state["data"][path][field]
    else:
        state["data"][path][field] = value
    result = client.get("/api/v1/local/monitoring",
                        headers={"Authorization": "Bearer " + TOKEN})
    assert result.status_code == 503
    assert "http://127.0.0.1" not in result.text
    assert "http://127.0.0.1" not in result.text


@pytest.mark.parametrize("body,content_type", [
    (b"{" + b"a" * 20000 + b"}", "application/json"),
    (b"<html>internal exception</html>", "text/html"),
    (b"not-json", "application/json"),
])
def test_oversized_or_malformed_never_leaks_upstream(sandbox, body, content_type):
    client, state = sandbox
    state["body_override"] = body
    state["content_type"] = content_type
    result = client.get("/api/v1/local/monitoring",
                        headers={"Authorization": "Bearer " + TOKEN})
    assert result.status_code == 503
    assert result.json() == {"detail": "local inference monitoring unavailable"}
    assert b"internal exception" not in result.content


def test_zero_duration_observations_never_invents_mean(sandbox):
    client, state = sandbox
    data = state["data"]["/metrics/inference"]
    data["duration_observations_total"] = 0
    data["duration_total_us"] = 0
    result = client.get("/api/v1/local/monitoring",
                        headers={"Authorization": "Bearer " + TOKEN})
    assert result.status_code == 200
    assert result.json()["inference"]["mean_post_admission_duration_ms"] is None


def test_monitoring_unavailable_is_generic_local_503():
    adapter = GatewayMonitoring()
    adapter.ORIGIN = "http://127.0.0.1:1"  # deterministic refused loopback port
    with pytest.raises(HTTPException) as exc:
        adapter.snapshot()
    assert exc.value.status_code == 503
    assert exc.value.detail == "local inference monitoring unavailable"
