"""Synthetic private evidence fixtures for local-public transcription audit.

These tests cannot attest real Ollama execution or hardware.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.benchmark import audit_public_case_study, compare
from tools.benchmark.tests import test_compare


class PublishedEvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_compare.ComparisonTests(
            methodName="test_valid_results_are_labeled_exploratory_not_speedups")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.baseline = self.fixture.manifest(self.fixture.baseline)
        self.candidate = self.fixture.manifest(self.fixture.candidate)
        baseline = compare._read_experiment(self.baseline)
        candidate = compare._read_experiment(self.candidate)
        self.compared = compare.compare(
            self.baseline, self.candidate, "Auditing recorded local experiment")
        config = baseline["configuration"]
        samples = self.compared["quality"]["samples"]
        public = {
            "schema_version": 1,
            "kind": "developer_transcribed_real_local_aggregate",
            "provenance": {
                "comparator_status": self.compared["comparison_status"],
                "github_ci_revalidates_raw_hardware_reports": False,
            },
            "model": {
                "tag": baseline["manifest"]["model_tag"],
                "digest": self.compared["model_digest"],
                "quantization": baseline["manifest"]["quantization"],
            },
            "methodology": {
                "baseline_ollama_num_parallel": 1,
                "candidate_ollama_num_parallel": 2,
                "concurrency_levels": [1, 2, 4],
                "repeats_per_concurrency": config["repeats"],
                "measured_requests_per_run": config["requests_per_run"],
                "warmup_requests_per_run": config["warmup_per_run"],
                "max_completion_tokens": config["max_tokens"],
                "timeout_seconds": config["timeout_seconds"],
                "temperature": 0, "streaming": True,
                "benchmark_dataset": "docs/benchmarks/workload.v1.jsonl",
                "quality_dataset": "docs/nlp-eval/workload.v1.jsonl",
                "requests_per_configuration": config["repeats"] * config["requests_per_run"] * 3,
                "total_measured_requests_across_both_configurations":
                    config["repeats"] * config["requests_per_run"] * 6,
            },
            "quality": {},
            "metrics_by_concurrency": {},
        }
        for label in ("baseline", "candidate"):
            scores = self.compared["quality"][label]
            public["quality"][label] = {
                "samples": samples, "successful_requests": samples, "failed_requests": 0,
                "exact_correct": round(scores["exact_match"] * samples),
                "normalized_exact_correct": round(scores["normalized_exact_match"] * samples),
            }
        for c in ("1", "2", "4"):
            raw = self.compared["metrics_by_concurrency"][c]
            group = {
                "baseline": {}, "candidate": {}, "percentage_change": {},
                "successful_requests_per_configuration":
                    baseline["summary"][c]["successful_requests"],
                "failed_requests_per_configuration": 0,
            }
            for k in audit_public_case_study.METRICS:
                group["baseline"][k] = raw[k]["baseline_median"]
                group["candidate"][k] = raw[k]["candidate_median"]
                group["percentage_change"][k] = raw[k]["percentage_change"]
            public["metrics_by_concurrency"][c] = group
        self.published = self.root / "published.json"
        self.write_published(public)

    def write_published(self, value):
        self.published.write_text(json.dumps(value, indent=2))

    def record(self):
        return json.loads(self.published.read_text())

    def audit_with_fixture_fingerprints(self):
        with patch.object(audit_public_case_study, "_check_versioned_dataset") as checked:
            audit_public_case_study.audit(
                self.published, self.baseline, self.candidate)
        self.assertEqual(checked.call_count, 2)

    def test_consistent_synthetic_reports_audit(self):
        self.audit_with_fixture_fingerprints()

    def test_public_throughput_falsification_is_rejected(self):
        record = self.record()
        record["metrics_by_concurrency"]["2"]["percentage_change"]["requests_per_second"] += .01
        self.write_published(record)
        with self.assertRaisesRegex(ValueError, "c2 requests_per_second change"):
            self.audit_with_fixture_fingerprints()

    def test_public_quality_score_falsification_is_rejected(self):
        record = self.record()
        record["quality"]["candidate"]["normalized_exact_correct"] -= 1
        self.write_published(record)
        with self.assertRaisesRegex(ValueError, "candidate quality normalized_exact_match"):
            self.audit_with_fixture_fingerprints()

    def test_incomplete_or_corrupted_raw_run_is_rejected(self):
        path = self.fixture.candidate / "repeat-01-c1.json"
        data = json.loads(path.read_text())
        data["results"]["latency_ms"]["p95"] = 9999999
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.audit_with_fixture_fingerprints()

    def test_false_reproduction_provenance_is_rejected(self):
        record = self.record()
        record["provenance"]["github_ci_revalidates_raw_hardware_reports"] = True
        self.write_published(record)
        with self.assertRaisesRegex(ValueError, "provenance scope"):
            self.audit_with_fixture_fingerprints()

    def test_public_sample_accounting_is_rejected_if_inconsistent(self):
        record = self.record()
        record["methodology"]["total_measured_requests_across_both_configurations"] += 36
        self.write_published(record)
        with self.assertRaisesRegex(ValueError, "combined request count"):
            self.audit_with_fixture_fingerprints()

    def test_cli_sanitizes_errors_and_success_has_caveats(self):
        args = [
            "--published", str(self.published),
            "--baseline", str(self.baseline),
            "--candidate", str(self.candidate),
        ]
        stdout, stderr = StringIO(), StringIO()
        with patch.object(audit_public_case_study, "_check_versioned_dataset"):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(audit_public_case_study.main(args), 0)
        self.assertIn("consistency only", stdout.getvalue())
        self.assertNotIn(str(self.root), stdout.getvalue())

        broken = self.record()
        broken["metrics_by_concurrency"]["1"]["baseline"]["requests_per_second"] += 5
        self.write_published(broken)
        stdout, stderr = StringIO(), StringIO()
        with patch.object(audit_public_case_study, "_check_versioned_dataset"):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(audit_public_case_study.main(args), 2)
        self.assertIn("rejected", stderr.getvalue())
        self.assertNotIn(str(self.root), stderr.getvalue())

    def test_versioned_dataset_fingerprint_uses_checked_in_file_bytes(self):
        for label in sorted(audit_public_case_study.DATASETS):
            path = audit_public_case_study.ROOT / label
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            audit_public_case_study._check_versioned_dataset(label, digest)
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                audit_public_case_study._check_versioned_dataset(label, "a" * 64)
        with self.assertRaisesRegex(ValueError, "unexpected versioned dataset"):
            audit_public_case_study._check_versioned_dataset("../private.jsonl", "a" * 64)

    def test_rejects_mismatched_model_and_changed_generation_cap(self):
        original = self.record()
        for key, invalid in (("digest", "0" * 64), ("quantization", "Q8_0")):
            record = json.loads(json.dumps(original))
            record["model"][key] = invalid
            self.write_published(record)
            with self.assertRaises(ValueError):
                self.audit_with_fixture_fingerprints()
        record = json.loads(json.dumps(original))
        record["methodology"]["max_completion_tokens"] += 1
        self.write_published(record)
        with self.assertRaises(ValueError):
            self.audit_with_fixture_fingerprints()


if __name__ == "__main__":
    unittest.main()
