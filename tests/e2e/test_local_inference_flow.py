"""Cross-service smoke test: Go gateway process + Python benchmark client.

The backend is a deterministic fake; no GPU/model performance is measured.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]


class FakeModel(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if request["model"] == "reject":
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b'confidential fake error')
            return
        if request["model"] == "invalid-completion":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices":[],"private":"DO_NOT_LEAK_FIXTURE"}')
            return
        if request["model"] in {"stream-error", "stream-truncated"}:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"PRIVATE_PARTIAL_TOKEN"}}]}\n\n')
            self.wfile.flush()
            if request["model"] == "stream-error":
                self.wfile.write(b'data: {"error":{"message":"PRIVATE_UPSTREAM_ERROR"}}\n\n')
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
            return
        if request["stream"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in (
                b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
                b'data: {"choices":[{"delta":{"content":"synthetic reply"}}]}\n\n',
                b'data: {"choices":[],"usage":{"completion_tokens":2,"prompt_tokens":4}}\n\n',
                b'data: [DONE]\n\n',
            ):
                self.wfile.write(chunk)
                self.wfile.flush()
        else:
            data = json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "synthetic reply"}}],
                "usage": {"completion_tokens": 2, "prompt_tokens": 4},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


@contextmanager
def live_gateway(binary, upstream_port):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "METRIQEN_GATEWAY_BIND_ADDR": f"127.0.0.1:{port}",
           "METRIQEN_UPSTREAM_URL": f"http://127.0.0.1:{upstream_port}"}
    process = subprocess.Popen([str(binary)], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            if process.poll() is not None:
                raise RuntimeError("Go gateway exited during startup")
            try:
                with urlopen(base + "/health/live", timeout=.5) as response:
                    if response.status == 200:
                        break
            except (URLError, HTTPError, TimeoutError):
                time.sleep(.05)
        else:
            raise RuntimeError("Go gateway did not respond")
        yield base + "/v1/chat/completions"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class LocalInferenceE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.tmp.name) / "gateway"
        subprocess.run(["go", "build", "-o", str(cls.binary), "./cmd/server"],
                       cwd=ROOT / "services/inference-gateway", check=True,
                       capture_output=True, timeout=90)
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        cls.thread = threading.Thread(target=cls.fake.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown()
        cls.fake.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def benchmark(self, endpoint, *, stream, model="test-model"):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            dataset = folder / "dataset.jsonl"
            dataset.write_text('{"messages":[{"role":"user","content":"PRIVATE_TEST_PROMPT"}]}\n')
            output = folder / "measurements.json"
            cmd = [
                sys.executable, "-m", "tools.benchmark.runner",
                "--endpoint", endpoint, "--model", model,
                "--model-revision", "fake-v1", "--runtime", "fake-upstream",
                "--hardware", "CI-no-GPU", "--dataset", str(dataset),
                "--requests", "4", "--concurrency", "2",
                "--warmup", "0" if model in {"reject", "invalid-completion", "stream-error", "stream-truncated"} else "1",
                "--max-tokens", "32", "--timeout", "8", "--output", str(output),
            ]
            if stream:
                cmd.append("--stream")
            completed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=35)
            contents = output.read_text()
            for secret in ("PRIVATE_TEST_PROMPT", "synthetic reply", "confidential fake error", "PRIVATE_PARTIAL_TOKEN", "PRIVATE_UPSTREAM_ERROR"):
                self.assertNotIn(secret, contents)
            return completed.returncode, json.loads(contents)

    def test_streaming_measurement_through_go_gateway(self):
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            exit_code, report = self.benchmark(endpoint, stream=True)
        self.assertEqual(exit_code, 0)
        result = report["results"]
        self.assertEqual((result["requests"], result["successes"], result["failures"]), (4, 4, 0))
        self.assertEqual(result["completion_token_coverage"], 4)
        self.assertEqual(result["output_tokens"], 8)
        self.assertEqual(result["ttft_samples"], 4)
        self.assertGreater(result["ttft_ms"]["p95"], 0)
        self.assertEqual(len(result["observations"]), 4)

    def test_repeated_sweep_uses_live_gateway_and_preserves_all_nine_runs(self):
        """Real Go process, real Python CLI, deterministic fixture LLM only."""
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                dataset = folder / "sweep-dataset.jsonl"
                dataset.write_text(
                    '{"messages":[{"role":"user","content":"PRIVATE SWEEP FIXTURE PROMPT"}]}\n',
                    encoding="utf-8",
                )
                output = folder / "sweep-evidence"
                cmd = [
                    sys.executable, "-m", "tools.benchmark.sweep",
                    "--endpoint", endpoint, "--model", "fixture-model",
                    "--model-revision", "a" * 64, "--runtime", "fake-upstream",
                    "--hardware", "CI-no-GPU", "--dataset", str(dataset),
                    "--repeats", "3", "--requests", "2", "--warmup", "0",
                    "--max-tokens", "32", "--timeout", "8",
                    "--output-dir", str(output),
                ]
                run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=75)
                self.assertEqual(run.returncode, 0, run.stderr)
                files = sorted(output.glob("repeat-*.json"))
                self.assertEqual(len(files), 9)
                summary_text = (output / "sweep-summary.json").read_text(encoding="utf-8")
                self.assertNotIn("PRIVATE SWEEP FIXTURE PROMPT", summary_text)
                self.assertNotIn("synthetic reply", summary_text)
                summary = json.loads(summary_text)
                self.assertEqual(len(summary["run_files"]), 9)
                self.assertEqual(summary["configuration"]["concurrency"], [1, 2, 4])
                for level in ("1", "2", "4"):
                    results = summary["summary"][level]
                    self.assertEqual(results["runs"], 3)
                    self.assertEqual(results["successful_requests"], 6)
                    self.assertEqual(results["failed_requests"], 0)
                    self.assertEqual(results["completion_token_coverage"], 6)
                    self.assertGreater(results["output_tokens_per_second"]["median"], 0)

    def test_streaming_inband_error_is_not_successful_through_gateway(self):
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            exit_code, report = self.benchmark(endpoint, stream=True, model="stream-error")
        self.assertEqual(exit_code, 1)
        result = report["results"]
        self.assertEqual((result["successes"], result["failures"]), (0, 4))
        self.assertEqual(result["errors"], {"upstream_stream_error": 4})
        self.assertIsNone(result["output_tokens_per_second"])

    def test_streaming_truncation_is_not_successful_through_gateway(self):
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            exit_code, report = self.benchmark(endpoint, stream=True, model="stream-truncated")
        self.assertEqual(exit_code, 1)
        result = report["results"]
        self.assertEqual((result["successes"], result["failures"]), (0, 4))
        self.assertEqual(result["errors"], {"missing_stream_terminator": 4})
        self.assertIsNone(result["output_tokens_per_second"])

    def test_nonstream_ttft_is_not_invented(self):
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            exit_code, report = self.benchmark(endpoint, stream=False)
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["results"]["output_tokens"], 8)
        self.assertIsNone(report["results"]["ttft_ms"])

    def test_upstream_429_is_not_measured_as_success(self):
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            exit_code, report = self.benchmark(endpoint, stream=False, model="reject")
        self.assertEqual(exit_code, 1)
        result = report["results"]
        self.assertEqual(result["failures"], 4)
        self.assertEqual(result["errors"], {"http_429": 4})
        self.assertIsNone(result["output_tokens_per_second"])

    def test_malformed_upstream_response_is_a_gateway_failure(self):
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            exit_code, report = self.benchmark(endpoint, stream=False, model="invalid-completion")
        self.assertEqual(exit_code, 1)
        result = report["results"]
        self.assertEqual(result["successes"], 0)
        self.assertEqual(result["failures"], 4)
        self.assertEqual(result["errors"], {"http_502": 4})
        self.assertIsNone(result["output_tokens_per_second"])




    def test_gateway_recovers_after_upstream_rejection_protocol_error_and_truncated_stream(self):
        """Real Go gateway process recovers using an isolated fake model.

        This is a crash/recovery *contract*, not an actual Ollama, GPU,
        production IdP, Redis or cloud failover exercise.
        """
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            root = endpoint.removesuffix("/v1/chat/completions")

            def chat(model, *, stream=False):
                body = json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": "PRIVATE_OPERATOR_PROMPT"}],
                    "stream": stream, "max_tokens": 32, "temperature": 0,
                }).encode()
                request = Request(endpoint, data=body, method="POST",
                                  headers={"Content-Type": "application/json"})
                try:
                    with urlopen(request, timeout=8) as response:
                        return response.status, response.read(65537)
                except HTTPError as exc:
                    try:
                        return exc.code, exc.read(65537)
                    finally:
                        exc.close()

            def metrics(path):
                with urlopen(root + path, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    return json.loads(response.read(16384))

            initial = metrics("/metrics/inference")
            self.assertEqual(initial["attempts_total"], 0)
            self.assertEqual(initial["active_attempts"], 0)

            rejected, body = chat("reject")
            self.assertEqual(rejected, 429)
            self.assertNotIn(b"confidential fake error", body)

            malformed, body = chat("invalid-completion")
            self.assertEqual(malformed, 502)
            self.assertNotIn(b"DO_NOT_LEAK_FIXTURE", body)

            recovered, body = chat("test-model")
            self.assertEqual(recovered, 200)
            self.assertTrue(json.loads(body)["choices"][0]["message"]["content"])

            interrupted, body = chat("stream-truncated", stream=True)
            self.assertEqual(interrupted, 200)
            self.assertNotIn(b"data: [DONE]", body)

            completed, body = chat("test-model", stream=True)
            self.assertEqual(completed, 200)
            self.assertIn(b"data: [DONE]\n\n", body)
            self.assertNotIn(b"PRIVATE_OPERATOR_PROMPT", body)

            outcomes = metrics("/metrics/inference")
            admission = metrics("/metrics/admission")
            upstream = metrics("/metrics/upstream")
            self.assertEqual(outcomes["attempts_total"], 5)
            self.assertEqual(outcomes["active_attempts"], 0)
            self.assertEqual(outcomes["nonstream_complete_total"], 1)
            self.assertEqual(outcomes["pre_response_failure_total"], 2)
            self.assertEqual(outcomes["stream_done_total"], 1)
            self.assertEqual(outcomes["stream_missing_done_total"], 1)
            self.assertEqual(outcomes["duration_observations_total"], 5)
            self.assertEqual(admission["active_requests"], 0)
            self.assertEqual(admission["waiting_requests"], 0)
            self.assertEqual(admission["admitted_immediate_total"], 5)
            self.assertGreaterEqual(upstream["http_rejected_total"], 1)
            self.assertGreaterEqual(upstream["protocol_errors_total"], 1)
            for counters in (outcomes, admission, upstream):
                serialized = json.dumps(counters)
                for secret in ("PRIVATE_OPERATOR_PROMPT", "PRIVATE_UPSTREAM_ERROR",
                               "confidential fake error", "DO_NOT_LEAK_FIXTURE"):
                    self.assertNotIn(secret, serialized)

    def test_nlp_quality_cli_uses_live_go_gateway(self):
        """Reference scoring uses the real gateway process, not direct fixture calls."""
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                dataset = folder / "quality.jsonl"
                dataset.write_text(
                    '{"id":"a","messages":[{"role":"user","content":"PRIVATE QUESTION ONE"}],"reference":"synthetic reply"}\n'
                    '{"id":"b","messages":[{"role":"user","content":"PRIVATE QUESTION TWO"}],"reference":"other answer"}\n',
                    encoding="utf-8",
                )
                output = folder / "quality-report.json"
                cmd = [
                    sys.executable, "-m", "tools.nlp_eval.online",
                    "--endpoint", endpoint, "--model", "fixture-model",
                    "--model-revision", "fixture-v1", "--runtime", "fake-upstream",
                    "--hardware", "CI-no-GPU", "--dataset", str(dataset),
                    "--max-tokens", "32", "--timeout", "8", "--output", str(output),
                ]
                finished = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=35)
                self.assertEqual(finished.returncode, 0, finished.stderr)
                text_report = output.read_text(encoding="utf-8")
                for secret in ("PRIVATE QUESTION", "synthetic reply", "other answer"):
                    self.assertNotIn(secret, text_report)
                report = json.loads(text_report)
                self.assertEqual(report["results"]["quality"]["sample_count"], 2)
                self.assertEqual(report["results"]["quality"]["exact_match"]["correct"], 1)
                self.assertEqual(report["results"]["quality"]["normalized_exact_match"]["correct"], 1)
                self.assertEqual(report["results"]["generation"]["successful_requests"], 2)

    def test_nlp_quality_cli_rejects_upstream_failure_without_partial_report(self):
        """Upstream failures must not produce misleading partial accuracy metrics."""
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                dataset = folder / "quality.jsonl"
                dataset.write_text(
                    '{"id":"a","messages":[{"role":"user","content":"PRIVATE QUESTION"}],"reference":"answer"}\n',
                    encoding="utf-8",
                )
                output = folder / "quality-report.json"
                cmd = [
                    sys.executable, "-m", "tools.nlp_eval.online",
                    "--endpoint", endpoint, "--model", "reject",
                    "--model-revision", "fixture-v1", "--runtime", "fake-upstream",
                    "--hardware", "CI-no-GPU", "--dataset", str(dataset),
                    "--max-tokens", "32", "--timeout", "8", "--output", str(output),
                ]
                finished = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=35)
                self.assertEqual(finished.returncode, 2, finished.stderr)
                self.assertFalse(output.exists())
                self.assertNotIn("confidential fake error", finished.stderr)



if __name__ == "__main__":
    unittest.main()
