"""Synthetic-only P5 cancel/recovery checks. Never contacts local Mac services."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools.demo import p5_stream_cancel_recovery as demo
from tools.demo import p5_stream_admission_smoke as metrics
from tools.demo import real_ollama as real

MODEL = "llama3.2:1b"
DIGEST = "f" * 64


def inference(phase: int) -> dict:
    terminal = 10 + phase
    return {
        "schema_version": 3,
        "attempts_total": terminal,
        "active_attempts": 0,
        "nonstream_complete_total": 3 + int(phase == 2),
        "stream_clean_eof_total": 7, "stream_done_total": 7,
        "stream_missing_done_total": 0, "pre_response_failure_total": 0,
        "post_response_interrupted_total": 0,
        "client_canceled_total": int(phase > 0),
        "duration_observations_total": terminal,
        "duration_bucket_counts": [terminal] + [0] * 8,
    }


def admission(phase: int) -> dict:
    return {
        "schema_version": 2, "active_requests": 0,
        "waiting_requests": 0, "max_active_requests": 4,
        "max_waiting_requests": 0,
        "admitted_immediate_total": 10 + phase,
        "admitted_queued_total": 0, "rejected_full_total": 0,
        "queue_timeout_total": 0, "queue_canceled_total": 0,
        "queue_wait_observations_total": 0,
    }


class Fake:
    def __init__(self):
        self.phase = 0
        self.stream_calls = 0
        self.post_calls = 0
        self.cancel_reported = True
        self.active_before = False
        self.after_active = False
        self.reject = False
        self.bad_tag = False

    def stream_once(self, *, model: str):
        if model != MODEL:
            raise AssertionError("wrong model")
        self.stream_calls += 1
        if self.stream_calls > 1:
            raise AssertionError("more than one streaming attempt")
        self.phase = 1

    def fetch(self, method, url, *, payload=None, stream=False):
        if method == "POST":
            if url != metrics.CHAT or self.phase != 1 or stream:
                raise AssertionError("only one post-cancel JSON chat allowed")
            self.post_calls += 1
            self.phase = 2
            return {"model": MODEL, "choices": [{"message": {"role": "assistant", "content": "READY"}}]}
        if method != "GET" or payload is not None or stream:
            raise AssertionError("invalid helper route")
        if url == metrics.UPSTREAM:
            return {"status": "reachable"}
        if url in (metrics.OLLAMA_TAGS, metrics.OLLAMA_LOADED):
            return {"models": [{"model": MODEL, "digest": "e" * 64 if self.bad_tag else DIGEST}]}
        if url == metrics.INFERENCE:
            d = inference(self.phase)
            if self.phase == 1 and not self.cancel_reported:
                d["client_canceled_total"] = 0
                d["pre_response_failure_total"] = 1
            if self.phase == 0 and self.active_before:
                d["active_attempts"] = 1
                d["attempts_total"] += 1
            if self.phase == 2 and self.after_active:
                d["active_attempts"] = 1
                d["attempts_total"] += 1
            return d
        if url == metrics.ADMISSION:
            d = admission(self.phase)
            if self.phase == 0 and self.active_before:
                d["active_requests"] = 1
            if self.phase == 2 and self.after_active:
                d["active_requests"] = 1
            if self.phase == 2 and self.reject:
                d["rejected_full_total"] = 1
            return d
        raise AssertionError("non-allowlisted URL")


class P5CancelTests(unittest.TestCase):
    def test_plan_only_zero_network(self):
        with patch.object(demo, "collect", side_effect=AssertionError("should not run")):
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(demo.main([]), 0)
        self.assertIn("PLAN ONLY", out.getvalue())

    def test_real_path_fake_fixtures_yield_narrow_observation(self):
        fake = Fake()
        doc = demo.collect(model=MODEL, digest=DIGEST,
                           fetch=fake.fetch, stream_once=fake.stream_once,
                           sleep=lambda _: None)
        self.assertEqual(fake.stream_calls, 1)
        self.assertEqual(fake.post_calls, 1)
        self.assertTrue(doc["passed"])
        self.assertFalse(doc["p5_reliability_security_gate_complete"])
        self.assertFalse(doc["performance_benchmark"])
        data = json.dumps(doc)
        self.assertNotIn(demo.CANCEL_PROMPT, data)
        self.assertNotIn(demo.VERIFY_PROMPT, data)
        self.assertNotIn(DIGEST, data)

    def test_busy_gateway_blocks_before_any_stream(self):
        fake = Fake()
        fake.active_before = True
        with self.assertRaisesRegex(demo.CancelBlocked, "gateway_not_idle_before_test"):
            demo.collect(model=MODEL, digest=DIGEST, fetch=fake.fetch,
                         stream_once=fake.stream_once)
        self.assertEqual(fake.stream_calls, 0)

    def test_failed_cancel_outcome_does_not_run_followup(self):
        fake = Fake()
        fake.cancel_reported = False
        with self.assertRaisesRegex(demo.CancelBlocked, "client_cancellation_not_observed"):
            demo.collect(model=MODEL, digest=DIGEST, fetch=fake.fetch,
                         stream_once=fake.stream_once, sleep=lambda _: None)
        self.assertEqual(fake.post_calls, 0)

    def test_after_phase_active_slot_rejected(self):
        fake = Fake()
        fake.after_active = True
        with self.assertRaisesRegex(demo.CancelBlocked, "gateway_not_idle_after_recovery"):
            demo.collect(model=MODEL, digest=DIGEST, fetch=fake.fetch,
                         stream_once=fake.stream_once, sleep=lambda _: None)

    def test_after_phase_rejected_full_count_regression(self):
        fake = Fake()
        fake.reject = True
        with self.assertRaisesRegex(demo.CancelBlocked, "post_cancel_rejection_delta_invalid"):
            demo.collect(model=MODEL, digest=DIGEST, fetch=fake.fetch,
                         stream_once=fake.stream_once, sleep=lambda _: None)

    def test_wrong_pinned_digest_fails_before_cancel(self):
        fake = Fake()
        fake.bad_tag = True
        with self.assertRaises(real.AcceptanceFailure):
            demo.collect(model=MODEL, digest=DIGEST, fetch=fake.fetch,
                         stream_once=fake.stream_once)
        self.assertEqual(fake.stream_calls, 0)

    def test_invalid_digest_fails_before_network(self):
        fake = Fake()
        with self.assertRaisesRegex(demo.CancelBlocked, "expected_digest_invalid"):
            demo.collect(model=MODEL, digest="bad", fetch=fake.fetch,
                         stream_once=fake.stream_once)
        self.assertEqual(fake.stream_calls, 0)

    def test_unreachable_gateway_fails_closed(self):
        fake = Fake()
        previous = fake.fetch
        def fetch(method, url, **kw):
            if url == metrics.UPSTREAM:
                return {"status": "unreachable"}
            return previous(method, url, **kw)
        with self.assertRaisesRegex(demo.CancelBlocked, "upstream_unreachable"):
            demo.collect(model=MODEL, digest=DIGEST, fetch=fetch,
                         stream_once=fake.stream_once)
        self.assertEqual(fake.stream_calls, 0)

    def test_execute_requires_explicit_digest_and_owner_private_output(self):
        with patch.object(demo, "collect", side_effect=AssertionError("bad run")):
            with redirect_stderr(StringIO()):
                self.assertEqual(demo.main(["--execute"]), 2)

    def test_private_output_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "reports" / "local" / "p5-cancel.json"
            original_collect = demo.collect
            def run(**kwargs):
                fake = Fake()
                return original_collect(**{**kwargs, "fetch": fake.fetch,
                                           "stream_once": fake.stream_once,
                                           "sleep": lambda _: None})
            with patch.object(real, "ROOT", root), patch.object(
                    demo, "collect", side_effect=run):
                args = ["--execute", "--model", MODEL, "--expected-digest", DIGEST,
                        "--output", str(target)]
                with redirect_stdout(StringIO()):
                    self.assertEqual(demo.main(args), 0)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
                self.assertFalse(json.loads(target.read_text())["p5_reliability_security_gate_complete"])
                with redirect_stderr(StringIO()):
                    self.assertEqual(demo.main(args), 2)


if __name__ == "__main__":
    unittest.main()
