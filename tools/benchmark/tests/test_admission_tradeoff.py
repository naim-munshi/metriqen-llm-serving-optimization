"""Synthetic, no-GPU tests for P3 FIFO overload evidence-review contracts."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.benchmark import admission_load, admission_tradeoff as tool

DIGEST = "a" * 64
DATASET = "b" * 64
TIME = datetime(2026, 10, 20, 0, 0, 0, tzinfo=timezone.utc)


def produce(root: Path, *, depth: int, start: datetime, rate: float) -> Path:
    root.mkdir()
    records, entries = [], []
    for i, (repeat, concurrency) in enumerate(admission_load.run_order(3)):
        submitted = 36
        failures = (3 if depth == 0 else 1) if concurrency >= 6 else 0
        succeeded = submitted - failures
        observations = [
            {"index": idx, "status": 200 if idx < succeeded else 429,
             "success": idx < succeeded, "latency_ms": 100.0}
            for idx in range(submitted)
        ]
        now = start + timedelta(seconds=i * 10)
        done = now + timedelta(seconds=6)
        deltas = {k: 0 for k in admission_load.COUNTERS}
        deltas["admitted_immediate_total"] = succeeded
        deltas["rejected_full_total"] = failures
        result = {
            "requests": submitted, "successes": succeeded, "failures": failures,
            "requests_per_second": rate, "latency_ms": {"p95": 200.0 if depth == 0 else 240.0},
            "ttft_ms": {"p95": 30.0 if depth == 0 else 35.0},
            "observations": observations,
        }
        label = f"repeat-{repeat:02d}-c{concurrency}.json"
        row = {
            "schema_version": 1, "repeat": repeat, "concurrency": concurrency,
            "started_at_utc": now.isoformat().replace("+00:00", "Z"),
            "finished_at_utc": done.isoformat().replace("+00:00", "Z"),
            "results": result, "http_429_observed": failures,
            "admission_deltas_including_warmup": deltas,
        }
        (root / label).write_text(json.dumps(row))
        records.append(row)
        entries.append({
            "schema_version": 1, "repeat": repeat, "concurrency": concurrency,
            "started_at_utc": row["started_at_utc"],
            "finished_at_utc": row["finished_at_utc"],
            "http_429_observed": failures,
            "admission_deltas_including_warmup": deltas,
            "file": label,
            "results": {k: result[k] for k in
                ("requests", "successes", "failures", "requests_per_second", "latency_ms", "ttft_ms")},
        })
    aggregate = {
        "schema_version": 1, "kind": "local_admission_overload_observation",
        "model_inference_attested": False,
        "performance_improvement_proven": False,
        "environment": {
            "endpoint": "http://127.0.0.1:8081/v1/chat/completions",
            "model": "llama3.2:1b", "model_revision_self_reported": DIGEST,
            "runtime_self_reported": "ollama-0.40.5",
            "hardware_self_reported": "Apple M2 Metal", "dataset_sha256": DATASET,
        },
        "configuration": {
            "expected_queue_depth": depth, "concurrency_levels": [4, 6, 8, 12],
            "repeats": 3, "requests_per_run": 36,
            "warmup_per_run": 2, "max_tokens": 128,
            "timeout_seconds": 120, "stream_usage_requested": True,
        },
        "run_files": entries, "summary": admission_load.summary(records),
    }
    (root / "admission-summary.json").write_text(json.dumps(aggregate))
    return root


class OverloadReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.private = Path(tmp.name) / "reports/local"
        self.private.mkdir(parents=True)
        p = patch.object(tool.phase_run, "PRIVATE", self.private)
        p.start()
        self.addCleanup(p.stop)
        self.a = produce(self.private / "A", depth=0, start=TIME, rate=2.0)
        self.b = produce(self.private / "B", depth=2, start=TIME+timedelta(minutes=10), rate=3.0)

    def test_valid_tradeoff_reports_all_levels_and_adverse_latency_without_success_claim(self):
        result = tool.analyze(self.a, self.b)
        self.assertFalse(result["portfolio_p3_complete"])
        self.assertFalse(result["causal_speedup_verified"])
        self.assertEqual(result["model_quality_verified"], False)
        self.assertEqual(set(result["observed_levels"]), set(tool.LEVELS))
        self.assertEqual(result["observed_levels"]["6"]["goodput_rps_change_pct"], 50)
        self.assertIn("c6_success_only_p95_latency_higher", result["adverse_flags"])
        self.assertLess(result["observed_levels"]["6"]["candidate_http_429"],
                        result["observed_levels"]["6"]["baseline_http_429"])
        self.assertNotIn(DIGEST, json.dumps(result))

    def test_wrong_revision_blocks_comparison(self):
        p = self.b / "admission-summary.json"
        record = json.loads(p.read_text())
        record["environment"]["model_revision_self_reported"] = "c" * 64
        p.write_text(json.dumps(record))
        with self.assertRaisesRegex(tool.ReviewError, "environment_or_dataset_mismatch"):
            tool.analyze(self.a, self.b)

    def test_missing_raw_or_tampered_summary_fails_closed(self):
        (self.a / "repeat-01-c4.json").unlink()
        with self.assertRaisesRegex(tool.ReviewError, "unsafe_missing"):
            tool.analyze(self.a, self.b)

    def test_mismatched_actual_429_does_not_pass(self):
        p = self.b / "repeat-01-c6.json"
        record = json.loads(p.read_text())
        record["http_429_observed"] += 1
        p.write_text(json.dumps(record))
        with self.assertRaisesRegex(tool.ReviewError, "raw_run_summary_mismatch"):
            tool.analyze(self.a, self.b)

    def test_more_than_queue_depth_changed_is_ineligible(self):
        p = self.b / "admission-summary.json"
        record = json.loads(p.read_text())
        record["configuration"]["max_tokens"] = 64
        p.write_text(json.dumps(record))
        with self.assertRaisesRegex(tool.ReviewError, "multiple_experimental_configuration_changes"):
            tool.analyze(self.a, self.b)

    def test_wrong_queue_depth_or_run_count_fails(self):
        p = self.b / "admission-summary.json"
        record = json.loads(p.read_text())
        record["configuration"]["expected_queue_depth"] = 0
        p.write_text(json.dumps(record))
        with self.assertRaisesRegex(tool.ReviewError, "invalid_overload_summary_profile"):
            tool.analyze(self.a, self.b)

    def test_nonprivate_and_symlink_inputs_rejected(self):
        fake = self.private.parent.parent / "external"
        fake.mkdir()
        with self.assertRaisesRegex(tool.ReviewError, "private_directory"):
            tool.analyze(fake, self.b)
        link = self.private / "link"
        link.symlink_to(self.a, target_is_directory=True)
        with self.assertRaisesRegex(tool.ReviewError, "private_directory"):
            tool.analyze(link, self.b)

    def test_output_is_exclusive_owner_only_and_never_claims_p3(self):
        target = self.private / "comparison.json"
        args = ["--baseline-dir", str(self.a), "--candidate-dir", str(self.b),
                "--output", str(target)]
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(0, tool.main(args))
        self.assertIn("descriptive only", output.getvalue())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertFalse(json.loads(target.read_text())["portfolio_p3_complete"])
        with redirect_stderr(StringIO()):
            self.assertEqual(2, tool.main(args))


if __name__ == "__main__":
    unittest.main()
