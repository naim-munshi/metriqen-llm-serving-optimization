"""Synthetic-only guard tests for the optional real P5 one-stream smoke."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools.demo import p5_stream_admission_smoke as smoke
from tools.demo import real_ollama as real

MODEL = "llama3.2:1b"
DIGEST = "a" * 64
SSE = b'data: {"choices":[{"delta":{"content":"READY"}}]}\n\ndata: [DONE]\n\n'


def inference(*, completed=False):
    count = int(completed)
    return {
        "schema_version": 3, "attempts_total": 9 + count,
        "active_attempts": 0, "nonstream_complete_total": 2,
        "stream_clean_eof_total": 7 + count,
        "stream_done_total": 7 + count,
        "stream_missing_done_total": 0,
        "pre_response_failure_total": 0,
        "post_response_interrupted_total": 0,
        "client_canceled_total": 0,
        "duration_observations_total": 9 + count,
        "duration_bucket_counts": [9 + count] + [0] * 8,
    }


def admission(*, completed=False):
    count = int(completed)
    return {
        "schema_version": 2,
        "active_requests": 0, "waiting_requests": 0,
        "max_active_requests": 4, "max_waiting_requests": 0,
        "admitted_immediate_total": 9 + count,
        "admitted_queued_total": 0,
        "rejected_full_total": 0, "queue_timeout_total": 0,
        "queue_canceled_total": 0, "queue_wait_observations_total": 0,
    }


class FakeGateway:
    def __init__(self):
        self.inference_before = inference()
        self.inference_after = inference(completed=True)
        self.admission_before = admission()
        self.admission_after = admission(completed=True)
        self.sse = SSE
        self.calls = []
        self.sent = False

    def __call__(self, method, url, *, payload=None, stream=False):
        self.calls.append((method, url, payload, stream))
        if method == "POST":
            if self.sent or url != smoke.CHAT or not stream:
                raise AssertionError("only one SSE request is permitted")
            assert payload["model"] == MODEL
            assert payload["max_tokens"] == 24
            assert payload["stream"] is True
            self.sent = True
            return self.sse
        if method != "GET" or payload is not None or stream:
            raise AssertionError("unexpected request")
        if url == smoke.UPSTREAM:
            return {"status": "reachable", "probe": "ollama_api_version"}
        if url == smoke.OLLAMA_TAGS or url == smoke.OLLAMA_LOADED:
            return {"models": [{"name": MODEL, "digest": DIGEST}]}
        if url == smoke.INFERENCE:
            return dict(self.inference_after if self.sent else self.inference_before)
        if url == smoke.ADMISSION:
            return dict(self.admission_after if self.sent else self.admission_before)
        raise AssertionError("unapproved route")


class P5StreamSmokeTests(unittest.TestCase):
    def test_default_cli_is_zero_io_plan(self):
        with patch.object(smoke, "collect", side_effect=AssertionError("unexpected live inference")):
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(smoke.main([]), 0)
        self.assertIn("PLAN ONLY", output.getvalue())

    def test_opt_in_one_stream_and_accounting_succeeds_with_narrow_claims(self):
        fake = FakeGateway()
        doc = smoke.collect(model=MODEL, digest=DIGEST, fetch=fake)
        self.assertTrue(doc["passed"])
        self.assertEqual(len(doc["checks_passed"]), 8)
        self.assertTrue(fake.sent)
        self.assertEqual(sum(method == "POST" for method, _, _, _ in fake.calls), 1)
        self.assertFalse(doc["client_cancellation_tested"])
        self.assertFalse(doc["p5_reliability_security_gate_complete"])
        self.assertFalse(doc["performance_benchmark"])
        serialized = json.dumps(doc)
        self.assertNotIn("Reply with", serialized)
        self.assertNotIn("READY", serialized)
        self.assertNotIn(DIGEST, serialized)
        self.assertNotIn(SSE.decode(), serialized)

    def test_empty_or_missing_terminal_event_rejected(self):
        for value in (b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n',
                      b'data: [DONE]\n\n', b"", b'PRIVATE_UPSTREAM_TEXT'):
            with self.subTest(sse=value):
                fake = FakeGateway()
                fake.sse = value
                with self.assertRaises(real.AcceptanceFailure):
                    smoke.collect(model=MODEL, digest=DIGEST, fetch=fake)

    def test_not_idle_before_stream_fails_without_sending_chat(self):
        fake = FakeGateway()
        fake.admission_before["active_requests"] = 1
        with self.assertRaisesRegex(smoke.SmokeBlocked, "other_gateway_requests_active"):
            smoke.collect(model=MODEL, digest=DIGEST, fetch=fake)
        self.assertFalse(fake.sent)

    def test_active_slots_not_released_detected(self):
        fake = FakeGateway()
        fake.admission_after["active_requests"] = 1
        with self.assertRaisesRegex(smoke.SmokeBlocked, "gateway_slots_not_released"):
            smoke.collect(model=MODEL, digest=DIGEST, fetch=fake)

    def test_stream_counters_increased_wrongly_fails(self):
        fake = FakeGateway()
        fake.inference_after["attempts_total"] = 11
        fake.inference_after["stream_clean_eof_total"] = 9
        fake.inference_after["stream_done_total"] = 9
        fake.inference_after["duration_observations_total"] = 11
        fake.inference_after["duration_bucket_counts"][0] = 11
        with self.assertRaisesRegex(smoke.SmokeBlocked, "stream_inference_counters_unmatched"):
            smoke.collect(model=MODEL, digest=DIGEST, fetch=fake)

    def test_unsuccessful_upstream_or_tag_digest_refused(self):
        fake = FakeGateway()
        original = fake.__call__
        def unavailable(*a, **kw):
            if a[1] == smoke.UPSTREAM:
                return {"status": "unreachable"}
            return original(*a, **kw)
        with self.assertRaisesRegex(smoke.SmokeBlocked, "gateway_upstream_not_reachable"):
            smoke.collect(model=MODEL, digest=DIGEST, fetch=unavailable)
        self.assertFalse(fake.sent)
        with self.assertRaises(real.AcceptanceFailure):
            smoke.collect(model=MODEL, digest="b" * 64, fetch=FakeGateway())
        with self.assertRaisesRegex(smoke.SmokeBlocked, "model_digest_invalid"):
            smoke.collect(model=MODEL, digest="not-a-digest", fetch=FakeGateway())

    def test_admission_not_recorded_fails(self):
        fake = FakeGateway()
        fake.admission_after["admitted_immediate_total"] = 9
        with self.assertRaisesRegex(smoke.SmokeBlocked, "stream_admission_not_recorded"):
            smoke.collect(model=MODEL, digest=DIGEST, fetch=fake)

    def test_unexpected_failure_metric_fails(self):
        fake = FakeGateway()
        fake.admission_after["rejected_full_total"] = 1
        with self.assertRaisesRegex(smoke.SmokeBlocked, "unexpected_admission_failure"):
            smoke.collect(model=MODEL, digest=DIGEST, fetch=fake)

    def test_schema_and_inference_accounting_validation(self):
        fake = FakeGateway()
        fake.inference_before["schema_version"] = 2
        with self.assertRaisesRegex(smoke.SmokeBlocked, "inference_metrics_schema_mismatch"):
            smoke.collect(model=MODEL, digest=DIGEST, fetch=fake)
        fake = FakeGateway()
        fake.inference_before["duration_bucket_counts"] = [0] * 9
        with self.assertRaisesRegex(smoke.SmokeBlocked, "inference_duration_histogram_invalid"):
            smoke.collect(model=MODEL, digest=DIGEST, fetch=fake)

    def test_disallows_non_allowlisted_url(self):
        with self.assertRaisesRegex(smoke.SmokeBlocked, "endpoint_not_allowlisted"):
            smoke.fetch_local("GET", "https://example.com/private")

    def test_cli_requires_explicit_pinned_digest_and_private_output(self):
        with patch.object(smoke, "collect", side_effect=AssertionError("must not infer")):
            with redirect_stderr(StringIO()):
                self.assertEqual(smoke.main(["--execute"]), 2)
                self.assertEqual(smoke.main(["--execute", "--expected-digest", DIGEST]), 2)

    def test_cli_private_report_and_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "reports" / "local" / "p5-smoke.json"
            original_collect = smoke.collect
            with patch.object(real, "ROOT", root), patch.object(
                    smoke, "collect", side_effect=lambda **kw: original_collect(
                        **{**kw, "fetch": FakeGateway()})):
                args = ["--execute", "--model", MODEL, "--expected-digest", DIGEST,
                        "--output", str(path)]
                with redirect_stdout(StringIO()):
                    self.assertEqual(smoke.main(args), 0)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                doc = json.loads(path.read_text(encoding="utf-8"))
                self.assertFalse(doc["p5_reliability_security_gate_complete"])
                with redirect_stderr(StringIO()):
                    self.assertEqual(smoke.main(args), 2)


if __name__ == "__main__":
    unittest.main()
