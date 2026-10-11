"""Offline tests: local inference, quality scoring and privacy."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from tools.nlp_eval import online


class _ModelFixture(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if data["model"] == "reject":
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b"SECRET UPSTREAM ERROR")
            return
        if data["model"] == "redirect":
            self.send_response(302)
            self.send_header("Location", "https://example.invalid/steal")
            self.end_headers()
            return
        if data["model"] == "bad-schema":
            content = b'{"choices":[]}'
        else:
            assert data["stream"] is False
            assert data["temperature"] == 0
            assert data["max_tokens"] == 32
            content = json.dumps({
                "choices": [{"message": {"role": "assistant", "content": " THE ANSWER "}}]
            }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


@contextmanager
def fake_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class LocalQualityEvaluationTests(unittest.TestCase):
    def test_local_only_endpoint_validation(self):
        for bad in [
            "http://localhost:8081/v1/chat/completions",
            "http://example.com:8081/v1/chat/completions",
            "https://127.0.0.1:8081/v1/chat/completions",
            "http://127.0.0.1:8081/private",
            "http://127.0.0.1:8081/v1/chat/completions?token=test",
            "http://127.0.0.1:notaport/v1/chat/completions",
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                online.validate_endpoint(bad)
        online.validate_endpoint("http://127.0.0.1:8081/v1/chat/completions")

    def test_workload_rejects_duplicates_and_unrecognized_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "workload.jsonl"
            file.write_text('{"id":"x","messages":[{"role":"user","content":"q"}],"reference":"a"}\n'
                            '{"id":"x","messages":[{"role":"user","content":"q"}],"reference":"a"}\n')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                online.load_workload(file)
            file.write_text('{"id":"x","messages":[{"role":[],"content":"q"}],"reference":"a"}\n')
            with self.assertRaisesRegex(ValueError, "invalid message"):
                online.load_workload(file)
            file.write_text('{"id":"x","messages":[],"reference":"a","extra":"secret"}\n')
            with self.assertRaisesRegex(ValueError, "must contain"):
                online.load_workload(file)

    def _run(self, endpoint, *, model="fixture"):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            dataset, output = folder / "workload.jsonl", folder / "quality.json"
            dataset.write_text('{"id":"one","messages":[{"role":"user","content":"PRIVATE PROMPT"}],"reference":"the answer"}\n'
                               '{"id":"two","messages":[{"role":"user","content":"PRIVATE PROMPT 2"}],"reference":"WRONG REFERENCE"}\n')
            code = online.main([
                "--endpoint", endpoint, "--model", model,
                "--model-revision", "fixture-v1", "--runtime", "fake",
                "--hardware", "fixture-CPU", "--dataset", str(dataset),
                "--max-tokens", "32", "--timeout", "5", "--output", str(output),
            ])
            if output.exists():
                contents = output.read_text()
                for private in [
                    "PRIVATE PROMPT", "PRIVATE PROMPT 2", "THE ANSWER",
                    "WRONG REFERENCE", "SECRET UPSTREAM ERROR",
                ]:
                    self.assertNotIn(private, contents)
                report = json.loads(contents)
            else:
                report = None
            return code, report

    def test_end_to_end_local_generation_and_scoring(self):
        with fake_server() as endpoint:
            code, report = self._run(endpoint)
        self.assertEqual(code, 0)
        self.assertEqual(report["results"]["quality"]["sample_count"], 2)
        self.assertEqual(report["results"]["quality"]["exact_match"]["correct"], 0)
        self.assertEqual(report["results"]["quality"]["normalized_exact_match"]["correct"], 1)
        self.assertEqual(report["results"]["generation"]["successful_requests"], 2)
        self.assertEqual(report["decoding"], {"temperature": 0, "max_tokens": 32, "stream": False})
        self.assertEqual(len(report["dataset_sha256"]), 64)

    def test_upstream_429_aborts_without_report(self):
        with fake_server() as endpoint:
            code, report = self._run(endpoint, model="reject")
        self.assertEqual(code, 2)
        self.assertIsNone(report)

    def test_bad_schema_aborts_without_report(self):
        with fake_server() as endpoint:
            code, report = self._run(endpoint, model="bad-schema")
        self.assertEqual(code, 2)
        self.assertIsNone(report)

    def test_redirect_does_not_follow(self):
        with fake_server() as endpoint:
            code, report = self._run(endpoint, model="redirect")
        self.assertEqual(code, 2)
        self.assertIsNone(report)


if __name__ == "__main__":
    unittest.main()
