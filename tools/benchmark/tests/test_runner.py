"""Offline reproducible contract tests; no real LLM or benchmark claims."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from tools.benchmark import runner


class FakeChat(BaseHTTPRequestHandler):
    received = []
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).received.append(body)
        if body["model"] == "bad":
            self.send_response(429)
            self.end_headers()
            return
        if body["stream"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n')
            self.wfile.flush()
            time.sleep(.02)
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n')
            self.wfile.flush()
            self.wfile.write(b'data: {"usage":{"completion_tokens":2,"prompt_tokens":3}}\n\n')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        else:
            data = json.dumps({"choices": [{"message": {"content": "ok"}}],
                               "usage": {"completion_tokens": 4, "prompt_tokens": 7}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


@contextmanager
def server():
    FakeChat.received = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeChat)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/v1/chat/completions"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()


class BenchmarkRunnerTests(unittest.TestCase):
    def test_endpoint_rejects_nonlocal_and_redirect_url(self):
        for endpoint in ["https://127.0.0.1:8081/v1/chat/completions", "http://localhost:8081/v1/chat/completions",
                         "http://example.com:8081/v1/chat/completions", "http://127.0.0.1:8081/evil",
                         "http://127.0.0.1:8081/v1/chat/completions?x=y"]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                runner.validate_endpoint(endpoint)
        runner.validate_endpoint("http://127.0.0.1:8081/v1/chat/completions")

    def test_dataset_hash_and_reject_unexpected_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "dataset.jsonl"
            p.write_text('{"messages":[{"role":"user","content":"hello"}]}\n')
            rows, sha = runner.read_dataset(p)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(sha), 64)
            p.write_text('{"messages":[],"secret":"leak"}\n')
            with self.assertRaises(ValueError):
                runner.read_dataset(p)

    def test_nearest_rank_is_deterministic(self):
        self.assertEqual(runner.percentile_nearest_rank(list(range(1, 21)), .95), 19)
        self.assertIsNone(runner.percentile_nearest_rank([], .95))

    def test_streaming_ttft_is_first_content_not_first_event(self):
        with server() as endpoint:
            sample = runner.request_once(endpoint, "sample", [{"role":"user", "content":"test"}], stream=True, max_tokens=64, timeout=5)
        self.assertTrue(sample.success, sample.error)
        self.assertIsNotNone(sample.ttft_ms)
        self.assertGreater(sample.ttft_ms, 10)
        self.assertEqual(sample.completion_tokens, 2)

    def test_nonstream_completion_and_usage(self):
        with server() as endpoint:
            sample = runner.request_once(endpoint, "sample", [{"role":"user", "content":"test"}], stream=False, max_tokens=64, timeout=5)
        self.assertTrue(sample.success, sample.error)
        self.assertIsNone(sample.ttft_ms)
        self.assertEqual(sample.completion_tokens, 4)

    def test_warmup_excluded_and_concurrent_measurement(self):
        with server() as endpoint:
            result = runner.run(endpoint, "sample", [[{"role":"user","content":"hello"}]],
                                stream=False, max_tokens=64, concurrency=2, requests=4, warmup=2, timeout=5)
        self.assertEqual(result["requests"], 4)
        self.assertEqual(result["successes"], 4)
        self.assertEqual(result["output_tokens"], 16)
        self.assertEqual(len(FakeChat.received), 6)

    def test_http_errors_are_counted_not_misreported_as_successes(self):
        with server() as endpoint:
            result = runner.run(endpoint, "bad", [[{"role":"user","content":"hello"}]],
                                stream=False, max_tokens=64, concurrency=1, requests=3, warmup=0, timeout=5)
        self.assertEqual(result["failures"], 3)
        self.assertEqual(result["errors"], {"http_429": 3})
        self.assertIsNone(result["output_tokens_per_second"])

    def test_missing_token_usage_does_not_invent_throughput(self):
        samples = [runner.Sample(200, True, 100, 40, 10, 20), runner.Sample(200, True, 110, 45, None, None)]
        result = runner.summarize(samples, 1, True)
        self.assertEqual(result["completion_token_coverage"], 1)
        self.assertIsNone(result["output_tokens_per_second"])

    def test_command_outputs_report_without_prompts(self):
        with server() as endpoint, tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "dataset.jsonl"
            output = Path(directory) / "run.json"
            dataset.write_text('{"messages":[{"role":"user","content":"private fixture prompt"}]}\n')
            args = ["--endpoint", endpoint, "--dataset", str(dataset), "--model", "sample", "--runtime", "mock",
                    "--hardware", "mock-cpu", "--model-revision", "mock-rev", "--requests", "2", "--warmup", "1", "--output", str(output)]
            self.assertEqual(runner.main(args), 0)
            report = output.read_text()
            self.assertNotIn("private fixture prompt", report)
            self.assertEqual(json.loads(report)["results"]["successes"], 2)


if __name__ == "__main__":
    unittest.main()
