"""Offline admission-load regression tests; no live LLM inference."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.benchmark import admission_load


class AdmissionLoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.dataset = root / "workload.jsonl"
        self.dataset.write_text(
            '{"messages":[{"role":"user","content":"DO_NOT_PUBLISH_PROMPT"}]}\n',
            encoding="utf-8",
        )
        self.output = root / "experiment"
        self.args = argparse.Namespace(
            endpoint="http://127.0.0.1:8081/v1/chat/completions",
            model="llama3.2:1b", model_revision="a" * 64,
            runtime="ollama-0.30.8", hardware="Apple M2 (test label)",
            dataset=self.dataset, expected_queue_depth=2,
            repeats=3, requests=12, warmup=1, max_tokens=32,
            timeout=30, no_stream_usage=False, output_dir=self.output,
        )
        self.snap_count = 0

    def snapshot(self, endpoint, expected):
        self.snap_count += 1
        value = self.snap_count
        return {
            "admitted_immediate_total": value * 10,
            "admitted_queued_total": value * 2,
            "rejected_full_total": value * 3,
            "queue_timeout_total": 0,
            "queue_canceled_total": 0,
        }

    @staticmethod
    def result(endpoint, model, samples, *, stream, max_tokens, concurrency,
               requests, warmup, timeout, include_usage):
        assert stream is True
        assert concurrency in admission_load.LEVELS
        observations = [
            {"index": n, "success": n < requests - 2,
             "status": 200 if n < requests - 2 else 429,
             "error": None if n < requests - 2 else "http_429"}
            for n in range(requests)
        ]
        return {
            "requests": requests, "successes": requests - 2, "failures": 2,
            "requests_per_second": 1.25, "wall_seconds": 8.0,
            "latency_ms": {"p50": 50.0, "p95": 100.0},
            "ttft_ms": {"p50": 20.0, "p95": 60.0},
            "observations": observations,
        }

    def test_rotating_order(self):
        self.assertEqual(admission_load.run_order(2), [
            (1, 4), (1, 6), (1, 8), (1, 12),
            (2, 6), (2, 8), (2, 12), (2, 4),
        ])

    def test_measured_overload_retains_every_outcome(self):
        with (patch.object(admission_load, "admission_snapshot", side_effect=self.snapshot),
              patch.object(admission_load.runner, "run", side_effect=self.result) as mock):
            summary_path = admission_load.run(self.args)
        self.assertEqual(mock.call_count, 12)
        self.assertEqual(len(list(self.output.glob("repeat-*.json"))), 12)
        report = json.loads(summary_path.read_text())
        self.assertEqual(report["kind"], "local_admission_overload_observation")
        self.assertFalse(report["performance_improvement_proven"])
        for concurrency in ("4", "6", "8", "12"):
            result = report["summary"][concurrency]
            self.assertEqual(result["submitted_requests"], 36)
            self.assertEqual(result["successes"], 30)
            self.assertEqual(result["failures"], 6)
            self.assertEqual(result["http_429_observed"], 6)
            self.assertEqual(result["success_rate"], 0.8333)
            self.assertEqual(result["admission_counter_deltas_including_warmup"]["rejected_full_total"], 9)
        self.assertNotIn("DO_NOT_PUBLISH_PROMPT", summary_path.read_text())
        self.assertNotIn("DO_NOT_PUBLISH_PROMPT", (self.output / "repeat-01-c4.json").read_text())

    def test_wrong_queue_configuration_prevents_all_traffic(self):
        def wrong_depth(_endpoint, expected):
            raise ValueError("gateway capacity/queue configuration does not match expected values")
        with (patch.object(admission_load, "admission_snapshot", side_effect=wrong_depth),
              patch.object(admission_load.runner, "run") as mock):
            with self.assertRaisesRegex(ValueError, "configuration"):
                admission_load.run(self.args)
            mock.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_unsafe_endpoint_rejected(self):
        self.args.endpoint = "http://example.com/v1/chat/completions"
        with patch.object(admission_load, "admission_snapshot") as mock:
            with self.assertRaisesRegex(ValueError, "127.0.0.1"):
                admission_load.run(self.args)
            mock.assert_not_called()

    def test_existing_evidence_is_not_overwritten(self):
        self.output.mkdir()
        (self.output / "keep.json").write_text("original")
        with (patch.object(admission_load, "admission_snapshot", side_effect=self.snapshot),
              patch.object(admission_load.runner, "run") as mock):
            with self.assertRaises(FileExistsError):
                admission_load.run(self.args)
            mock.assert_not_called()
        self.assertEqual((self.output / "keep.json").read_text(), "original")

    def test_counter_reset_blocks_aggregate(self):
        snapshots = [
            {key: 10 for key in admission_load.COUNTERS},
            {key: 10 for key in admission_load.COUNTERS},
            {key: 0 for key in admission_load.COUNTERS},
        ]
        with (patch.object(admission_load, "admission_snapshot", side_effect=snapshots),
              patch.object(admission_load.runner, "run", side_effect=self.result)):
            with self.assertRaisesRegex(ValueError, "reset"):
                admission_load.run(self.args)
        self.assertFalse((self.output / "admission-summary.json").exists())

    def test_midrun_failure_keeps_previous_evidence_without_summary(self):
        calls = 0
        def fail_after_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RuntimeError("injected transport failure")
            return self.result(*args, **kwargs)
        with (patch.object(admission_load, "admission_snapshot", side_effect=self.snapshot),
              patch.object(admission_load.runner, "run", side_effect=fail_after_first)):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                admission_load.run(self.args)
        self.assertTrue((self.output / "repeat-01-c4.json").exists())
        self.assertFalse((self.output / "admission-summary.json").exists())

    def test_invalid_digest_or_sample_count(self):
        for digest, requests in [("unverified", 12), ("a" * 64, 11)]:
            self.args.model_revision = digest
            self.args.requests = requests
            with self.assertRaises(ValueError):
                admission_load.validate(self.args)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
