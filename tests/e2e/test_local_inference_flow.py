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
from urllib.request import urlopen

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
            chunks = [
                b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
                b'data: {"choices":[{"delta":{"content":"synthetic reply"}}]}\n\n',
            ]
            if request.get("stream_options") == {"include_usage": True}:
                chunks.append(b'data: {"choices":[],"usage":{"completion_tokens":2,"prompt_tokens":4}}\n\n')
            chunks.append(b'data: [DONE]\n\n')
            for chunk in chunks:
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

    def benchmark(self, endpoint, *, stream, model="test-model", request_usage=True):
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
                if not request_usage:
                    cmd.append("--no-stream-usage")
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
        self.assertTrue(report["configuration"]["stream_usage_requested"])
        self.assertEqual(result["completion_token_coverage"], 4)
        self.assertEqual(result["output_tokens"], 8)
        self.assertGreater(result["output_tokens_per_second"], 0)
        self.assertEqual(result["ttft_samples"], 4)
        self.assertGreater(result["ttft_ms"]["p95"], 0)
        self.assertEqual(len(result["observations"]), 4)

    def test_stream_usage_disabled_is_recorded_and_not_invented(self):
        with live_gateway(self.binary, self.fake.server_port) as endpoint:
            exit_code, report = self.benchmark(endpoint, stream=True, request_usage=False)
        self.assertEqual(exit_code, 0)
        result = report["results"]
        self.assertFalse(report["configuration"]["stream_usage_requested"])
        self.assertEqual(result["successes"], 4)
        self.assertEqual(result["completion_token_coverage"], 0)
        self.assertIsNone(result["output_tokens"])
        self.assertIsNone(result["output_tokens_per_second"])

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
