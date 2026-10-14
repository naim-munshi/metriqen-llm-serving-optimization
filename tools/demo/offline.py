"""Zero-model-download portfolio demonstration with the actual Go gateway.

All generation uses a deterministic loopback fixture, never Ollama or a GPU.
Synthetic observations are not model benchmarks or optimization evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "services" / "inference-gateway"
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


# A demo probe never silently follows an upstream redirect or environment proxy.
OPENER = build_opener(ProxyHandler({}), NoRedirect())


def fetch(url, *, payload=None, timeout=10):
    request = Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"} if payload is not None else {},
        method="POST" if payload is not None else "GET",
    )
    try:
        with OPENER.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as error:
        try:
            return error.code, dict(error.headers), error.read()
        finally:
            error.close()


def chat(model="fixture:success", *, stream=False):
    # Private synthetic sentinel must never appear in stdout or reports.
    return {
        "model": model,
        "messages": [{"role": "user", "content": "PRIVATE_DEMO_PROMPT_7286"}],
        "stream": stream,
        "temperature": 0,
        "max_tokens": 32,
    }


class FixtureState:
    def __init__(self):
        self.release = threading.Event()
        self.mu = threading.Lock()
        self.calls = 0
        self.held = 0

    def record(self, model):
        with self.mu:
            self.calls += 1
            if model == "fixture:hold":
                self.held += 1

    def snapshot(self):
        with self.mu:
            return self.calls, self.held


class FixtureHandler(BaseHTTPRequestHandler):
    state = None

    def log_message(self, *_):
        pass  # Never log private fixture prompts or model responses.

    def do_GET(self):
        if self.path != "/api/version":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"version":"synthetic-fixture"}')

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = request["model"]
        self.state.record(model)
        if model == "fixture:hold":
            self.state.release.wait(timeout=12)
        if model == "fixture:reject":
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b"PRIVATE_FIXTURE_REJECTION_DO_NOT_LEAK")
            return
        if model == "fixture:malformed":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices":[],"secret":"DO_NOT_LEAK"}')
            return
        if request.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in (
                b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
                b'data: {"choices":[{"delta":{"content":"fixture-token"}}]}\n\n',
                b"data: [DONE]\n\n",
            ):
                self.wfile.write(chunk)
                self.wfile.flush()
            return
        result = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "fixture-only reply"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 3},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)


def unused_loopback_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def gateway(binary, upstream_port):
    port = unused_loopback_port()
    env = dict(os.environ)
    env.update({
        "METRIQEN_GATEWAY_BIND_ADDR": f"127.0.0.1:{port}",
        "METRIQEN_UPSTREAM_URL": f"http://127.0.0.1:{upstream_port}",
        "METRIQEN_ALLOWED_MODELS": "fixture:success,fixture:reject,fixture:malformed,fixture:hold",
        "METRIQEN_QUEUE_DEPTH": "2",
    })
    process = subprocess.Popen(
        [str(binary)], cwd=GATEWAY_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("gateway_startup_failed")
            try:
                if fetch(base + "/health/live", timeout=0.2)[0] == 200:
                    break
            except (URLError, TimeoutError, OSError):
                time.sleep(0.05)
        else:
            raise RuntimeError("gateway_startup_timeout")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def wait_metric(base, key, expected, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, _, raw = fetch(base + "/metrics/admission")
        if code == 200 and json.loads(raw)[key] == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"metric_{key}_did_not_reach_{expected}")


def verify(condition, label, results):
    if not condition:
        raise AssertionError(label)
    results.append(label)


def run_demo(base, state):
    checks = []
    def check(label, condition):
        verify(condition, label, checks)

    status, _, raw = fetch(base + "/health/ready")
    check("gateway_process_ready", status == 200 and json.loads(raw)["status"] == "ready")
    status, _, raw = fetch(base + "/health/upstream")
    check("synthetic_upstream_api_reachable", status == 200 and json.loads(raw)["status"] == "reachable")

    endpoint = base + "/v1/chat/completions"
    status, _, raw = fetch(endpoint, payload=chat())
    check("nonstream_completion", status == 200
          and json.loads(raw)["choices"][0]["message"]["content"] == "fixture-only reply")

    status, headers, raw = fetch(endpoint, payload=chat(stream=True))
    check("streaming_sse_terminator", status == 200
          and headers.get("Content-Type", "").startswith("text/event-stream")
          and b'fixture-token' in raw and b"data: [DONE]" in raw)

    initial_calls, _ = state.snapshot()
    status, _, raw = fetch(endpoint, payload=chat("fixture:not-allowed"))
    subsequent_calls, _ = state.snapshot()
    check("allowlist_blocks_upstream_forwarding", status == 403
          and json.loads(raw)["error"]["code"] == "model_not_allowed"
          and subsequent_calls == initial_calls)

    status, _, raw = fetch(endpoint, payload=chat("fixture:reject"))
    check("upstream_429_sanitized", status == 429
          and json.loads(raw)["error"]["code"] == "upstream_rejected"
          and b"PRIVATE_FIXTURE_REJECTION" not in raw)

    status, _, raw = fetch(endpoint, payload=chat("fixture:malformed"))
    check("invalid_completion_blocked", status == 502
          and json.loads(raw)["error"]["code"] == "upstream_protocol"
          and b"DO_NOT_LEAK" not in raw)

    status, _, raw = fetch(base + "/metrics/upstream")
    upstream = json.loads(raw)
    check("upstream_failure_metrics", status == 200
          and upstream["http_rejected_total"] == 1
          and upstream["protocol_errors_total"] == 1
          and upstream["connection_refused_total"] == 0)

    # Hold four actual upstream requests, queue two, then force an HTTP 429.
    # Polling real admission gauges removes dependence on model speed/sleeps.
    futures = []
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for _ in range(4):
                futures.append(pool.submit(fetch, endpoint, payload=chat("fixture:hold"), timeout=15))
            wait_metric(base, "active_requests", 4)
            wait_metric(base, "waiting_requests", 0)
            for expected in (1, 2):
                futures.append(pool.submit(fetch, endpoint, payload=chat("fixture:hold"), timeout=15))
                wait_metric(base, "waiting_requests", expected)
            status, headers, raw = fetch(endpoint, payload=chat("fixture:hold"))
            check("bounded_queue_429", status == 429
                  and headers.get("Retry-After") == "1"
                  and json.loads(raw)["error"]["code"] == "capacity_exceeded")
            _, held = state.snapshot()
            check("queued_requests_not_sent_to_upstream", held == 4)
            state.release.set()
            check("queued_requests_complete_after_release",
                  all(future.result(timeout=15)[0] == 200 for future in futures))
    finally:
        state.release.set()

    status, _, raw = fetch(base + "/metrics/admission")
    admission = json.loads(raw)
    check("admission_metrics_reconcile", status == 200
          and admission["active_requests"] == 0
          and admission["waiting_requests"] == 0
          and admission["max_active_requests"] == 4
          and admission["max_waiting_requests"] == 2
          and admission["admitted_queued_total"] == 2
          and admission["rejected_full_total"] == 1
          and admission["queue_wait_observations_total"] == 2)
    check("private_data_excluded_from_metrics", b"PRIVATE_DEMO_PROMPT_7286" not in raw)

    status, _, raw = fetch(base + "/metrics/inference")
    outcomes = json.loads(raw)
    check("inference_outcome_counters_reconcile", status == 200
          and outcomes["schema_version"] == 1
          and outcomes["attempts_total"] == 10
          and outcomes["active_attempts"] == 0
          and outcomes["nonstream_complete_total"] == 7
          and outcomes["stream_clean_eof_total"] == 1
          and outcomes["pre_response_failure_total"] == 2
          and outcomes["post_response_interrupted_total"] == 0
          and outcomes["client_canceled_total"] == 0
          and b"PRIVATE_DEMO_PROMPT_7286" not in raw)

    return {
        "schema_version": 1,
        "demo_kind": "synthetic_gateway_contract_demo",
        "runtime": "real_go_gateway_with_fake_local_model",
        "model_inference_executed": False,
        "performance_measurement": False,
        "passed": True,
        "checks_passed": len(checks),
        "checks": checks,
        "admission": {
            "immediate": admission["admitted_immediate_total"],
            "queued": admission["admitted_queued_total"],
            "rejected_full": admission["rejected_full_total"],
        },
        "upstream_failures": {
            "http_rejected": upstream["http_rejected_total"],
            "protocol": upstream["protocol_errors_total"],
        },
        "inference_outcomes": {
            "attempts": outcomes["attempts_total"],
            "nonstream_complete": outcomes["nonstream_complete_total"],
            "stream_clean_eof": outcomes["stream_clean_eof_total"],
            "pre_response_failure": outcomes["pre_response_failure_total"],
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Real Metriqen Go gateway and synthetic loopback upstream: no Ollama required"
    )
    parser.add_argument("--go", default="go", help="Go executable on PATH")
    args = parser.parse_args()
    state = FixtureState()
    handler = type("LocalFixture", (FixtureHandler,), {"state": state})
    fixture = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    fixture.daemon_threads = True
    thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="metriqen-offline-") as temp:
            binary = Path(temp) / "gateway"
            built = subprocess.run(
                [args.go, "build", "-o", str(binary), "./cmd/server"],
                cwd=GATEWAY_DIR, capture_output=True, timeout=120,
            )
            if built.returncode != 0:
                raise RuntimeError("go_build_failed")
            with gateway(binary, fixture.server_port) as base:
                report = run_demo(base, state)
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0
    except (AssertionError, RuntimeError, subprocess.TimeoutExpired,
            OSError, ValueError, KeyError, URLError) as error:
        # Do not output transport internals, prompt strings or upstream bodies.
        cause = str(error) if isinstance(error, (AssertionError, RuntimeError)) else type(error).__name__
        print("offline_demo_failed: " + cause, file=sys.stderr)
        return 1
    finally:
        state.release.set()
        fixture.shutdown()
        fixture.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
