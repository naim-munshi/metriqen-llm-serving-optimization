"""Synthetic contract tests only. No live local Ollama/Mac inference in CI."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tools.demo import real_ollama as smoke

MODEL = "llama3.2:1b"
DIGEST = "a" * 64
PRIVATE = "PRIVATE_USER_TEXT_AND_MODEL_OUTPUT_NEVER_PERSIST"


class FakeLocalRuntime:
    def __init__(self):
        self.calls = []
        self.model_digest = DIGEST
        self.loaded_digest = DIGEST
        self.native_text = "A short explanation from the fixture"
        self.json_text = "Another answer from fixture"
        self.stream_bytes = (b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                             b'data: [DONE]\n\n')
        self.metric_increment = True

    def __call__(self, method, url, *, payload=None, bearer=None, stream=False):
        self.calls.append((method, url, payload, bearer, stream))
        if url == smoke.OLLAMA + "/api/version":
            return {"version": "0.40.5"}
        if url in (smoke.OLLAMA + "/api/tags", smoke.OLLAMA + "/api/ps"):
            return {"models": [{"name": MODEL, "digest": (
                self.loaded_digest if url.endswith("/api/ps") else self.model_digest
            )}]}
        if url == smoke.GATEWAY + "/health/live":
            return {"service": "metriqen-inference-gateway", "status": "ok"}
        if url == smoke.GATEWAY + "/health/upstream":
            return {"status": "reachable"}
        if url == smoke.GATEWAY + "/metrics/inference":
            count = sum(1 for _, path, *_ in self.calls
                        if path == smoke.GATEWAY + "/metrics/inference")
            if count == 1:
                return {"attempts_total": 10, "nonstream_complete_total": 4,
                        "stream_done_total": 1}
            return {"attempts_total": 12 if self.metric_increment else 10,
                    "nonstream_complete_total": 5 if self.metric_increment else 4,
                    "stream_done_total": 2 if self.metric_increment else 1}
        if url == smoke.OLLAMA + "/api/chat":
            assert payload and payload["stream"] is False
            return {"model": MODEL, "done": True, "message": {"content": self.native_text}}
        if url == smoke.GATEWAY + "/v1/chat/completions" and not stream:
            return {"model": MODEL, "choices": [
                {"message": {"content": self.json_text}},
            ]}
        if url == smoke.GATEWAY + "/v1/chat/completions" and stream:
            return self.stream_bytes
        raise AssertionError("not an allowlisted test URL")


class LocalRealModelAcceptanceTests(unittest.TestCase):
    def test_complete_synthetic_flow_has_no_private_model_content_and_no_performance_claims(self):
        fake = FakeLocalRuntime()
        result = smoke.collect(model=MODEL, digest=DIGEST,
                               bearer="operator-token-local-secret", fetch=fake)
        self.assertTrue(result["passed"])
        self.assertTrue(result["model_inference_http_observed"])
        self.assertFalse(result["actual_gpu_execution_verified"])
        self.assertFalse(result["performance_benchmark"])
        self.assertEqual(len(result["tests_passed"]), 6)
        self.assertEqual(len(fake.calls), 11)
        self.assertTrue(all(u.startswith((smoke.OLLAMA, smoke.GATEWAY))
                            for _, u, _, _, _ in fake.calls))
        self.assertEqual([url for _, url, _, _, _ in fake.calls].count(
            smoke.OLLAMA + "/api/tags"), 2)
        self.assertTrue(all(bearer is None for _, u, _, bearer, _ in fake.calls
                            if u != smoke.GATEWAY + "/v1/chat/completions"))
        self.assertEqual(sum(1 for _, u, _, bearer, _ in fake.calls
                             if u == smoke.GATEWAY + "/v1/chat/completions"
                             and bearer == "operator-token-local-secret"), 2)
        raw = json.dumps(result)
        for value in (PRIVATE, fake.native_text, fake.json_text,
                      "operator-token-local-secret", "api/chat", "data:"):
            self.assertNotIn(value, raw)
        self.assertIsNotNone(datetime.fromisoformat(result["timestamp_utc"]))

    def test_wrong_installed_and_loaded_digest_or_model_rejected(self):
        for key, value, expected in [
            ("model_digest", "b"*64, "model_digest_unmatched"),
            ("loaded_digest", "c"*64, "model_digest_unmatched"),
            ("native_text", "", "native_generation_invalid"),
            ("json_text", "", "gateway_nonstream_generation_invalid"),
            ("metric_increment", False, "gateway_metric_did_not_increment"),
            ("stream_bytes", b'data: [DONE]\n\n', "stream_without_model_delta"),
        ]:
            with self.subTest(key=key):
                fixture = FakeLocalRuntime()
                setattr(fixture, key, value)
                with self.assertRaisesRegex(smoke.AcceptanceFailure, expected):
                    smoke.collect(model=MODEL, digest=DIGEST, fetch=fixture)

    def test_bad_cli_model_and_digest_fail_before_any_network(self):
        for name, digest in [
            ("/bad/model:tag", DIGEST),
            ("https://example.com/foo:bar", DIGEST),
            ("../model:1b", DIGEST),
            ("llama3.2:1b", "c" * 63),
            ("llama3.2:1b", "Z" * 64),
            ("llama3.2:1b", True),
        ]:
            with self.subTest(name=name, digest=digest):
                fake = FakeLocalRuntime()
                with self.assertRaises(smoke.AcceptanceFailure):
                    smoke.collect(model=name, digest=digest, fetch=fake)
                self.assertEqual(fake.calls, [])

    def test_sse_terminal_and_malformed_in_band_events_rejected(self):
        good = b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        cases = [
            (good + b'data: [DONE]\n\n', "stream_without_model_delta"),
            (b'data: [DONE]\n\n' + good, "stream_data_after_done"),
            (b'data: [DONE]\n\ndata: [DONE]\n\n', "stream_data_after_done"),
            (b'data: {"error":"PRIVATE_SSE_SECRET"}\n\ndata: [DONE]\n\n',
             "stream_invalid_event"),
            (b'data: {"choices":[{"delta":{"content":"yes"}}]}\n\n',
             "stream_missing_done"),
            (b'data: {"choices":[{"delta":{"content":"yes"}}]}\n\ndata: [DONE]\n',
             "stream_missing_final_separator"),
            (b'data: {"choices":[{"delta":{"content":"yes"}}]}\n\ndata: [DONE]\n\n'
             + b'data: {"choices": []}\n\n', "stream_data_after_done"),
            (b'\xff\xfe', "stream_invalid_utf8"),
        ]
        for sse, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(smoke.AcceptanceFailure, error):
                    smoke._check_stream(sse)
        smoke._check_stream(
            b'data: {"choices":[{"delta":{"content":"hi"}}]}\\n\\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\\n\\n'
            b'data: [DONE]\\n\\n')
        with self.assertRaisesRegex(smoke.AcceptanceFailure, "stream_data_after_usage"):
            smoke._check_stream(
                b'data: {"choices":[{"delta":{"content":"hi"}}]}\\n\\n'
                b'data: {"choices":[],"usage":{}}\\n\\n'
                b'data: {"choices":[{"delta":{"content":"late"}}]}\\n\\n'
                b'data: [DONE]\\n\\n')
        smoke._check_stream(b': keepalive\r\n\r\n'
                            b'data: {"choices":[{"delta":{"content":"ok"}}]}\r\n\r\n'
                            b'data: [DONE]\r\n\r\n')

    def test_allowlisted_fixed_url_and_credentials(self):
        for verb, url, payload, bearer in [
            ("GET", "https://example.com/api/version", None, None),
            ("GET", "http://localhost:11434/api/version", None, None),
            ("POST", smoke.OLLAMA + "/api/chat", None, None),
            ("POST", smoke.GATEWAY + "/v1/chat/completions", {}, None),
            ("GET", smoke.OLLAMA + "/api/tags", None, "leak-secret"),
            ("GET", smoke.GATEWAY + "/health/live", None, "leak-secret"),
        ]:
            with self.subTest(url=url, bearer=bearer):
                if verb == "POST" and payload == {}:
                    continue  # This one is intentionally allowlisted.
                with self.assertRaises(smoke.AcceptanceFailure):
                    smoke.fetch_local(verb, url, payload=payload, bearer=bearer)
        self.assertNotIn("http_proxy", str(smoke._OPENER.handlers).lower())

    def test_fixed_sanitized_http_error_does_not_echo_untrusted_body(self):
        class ResponseLike:
            def close(self): pass

        class Denying:
            def open(self, req, timeout):
                raise HTTPError(req.full_url, 401, PRIVATE, {}, BytesIO(PRIVATE.encode()))
        with patch.object(smoke, "_OPENER", Denying()):
            with self.assertRaisesRegex(smoke.AcceptanceFailure, "local_http_status_401") as err:
                smoke.fetch_local("GET", smoke.OLLAMA + "/api/version")
            self.assertNotIn(PRIVATE, str(err.exception))

    def test_private_report_only_under_ignored_location_never_overwrites(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            with patch.object(smoke, "ROOT", root):
                result = smoke.collect(model=MODEL, digest=DIGEST, fetch=FakeLocalRuntime())
                target = root / "reports/local/check.json"
                smoke.private_report(target, result)
                self.assertEqual(json.loads(target.read_text()), result)
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                with self.assertRaisesRegex(smoke.AcceptanceFailure, "create_failed"):
                    smoke.private_report(target, result)
                for path in (root / "public.json", root / "reports/public.json",
                             root / "reports/local/check.txt"):
                    with self.assertRaisesRegex(smoke.AcceptanceFailure, "private_reports_local"):
                        smoke.private_report(path, result)
                    self.assertFalse(path.exists())

    def test_main_no_false_success_when_real_services_not_available(self):
        with patch.object(smoke, "collect", side_effect=smoke.AcceptanceFailure(
            "local_runtime_unreachable")):
            with patch("sys.stderr", new_callable=StringIO) as err:
                with patch("sys.stdout", new_callable=StringIO) as out:
                    result = smoke.main(["--expected-digest", DIGEST])
            self.assertEqual(result, 2)
            self.assertIn("local_runtime_unreachable", err.getvalue())
            self.assertEqual(out.getvalue(), "")
        with patch.object(smoke, "collect", return_value={
            "passed": True, "model_inference_http_observed": True,
        }):
            with patch("sys.stdout", new_callable=StringIO) as out:
                self.assertEqual(smoke.main(["--expected-digest", DIGEST]), 0)
            self.assertIn("Not verified: GPU", out.getvalue())


if __name__ == "__main__":
    unittest.main()
