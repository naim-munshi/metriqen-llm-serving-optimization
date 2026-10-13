"""Offline A/B/A descriptive checks using synthetic, internally consistent raw reports.

The fixtures do not execute a model or prove the operator phase labels.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest

from tools.benchmark import aba_analysis
from tools.benchmark.tests import test_compare


class ABAAnalysisTests(unittest.TestCase):
    def setUp(self):
        fixture = test_compare.ComparisonTests(
            methodName="test_valid_results_are_labeled_exploratory_not_speedups"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.a1 = fixture.manifest(fixture.baseline)
        self.b = fixture.manifest(fixture.candidate)
        self.a2_dir = fixture.root / "returned-to-one-slot"
        fixture.write_bundle(self.a2_dir, wall=1.05)
        self.a2 = fixture.manifest(self.a2_dir)

    def test_three_phase_strict_comparison_and_all_six_metrics(self):
        record = aba_analysis.analyze(self.a1, self.b, self.a2)
        self.assertEqual(record["kind"], "human_attested_local_aba_reversion")
        self.assertEqual(record["status"], "exploratory_small_sample")
        self.assertEqual(set(record["metrics_by_concurrency"]), {"1", "2", "4"})
        self.assertEqual(set(record["metrics_by_concurrency"]["2"]), set(aba_analysis.METRICS))
        c2 = record["metrics_by_concurrency"]["2"]["requests_per_second"]
        self.assertEqual(c2["A1_median"], 2.0)
        self.assertEqual(c2["B_median"], 4.0)
        self.assertAlmostEqual(c2["A2_median"], round(2 / 1.05, 4), places=4)
        self.assertEqual(c2["A1_to_B_percentage_change"], 100.0)
        self.assertLess(c2["A1_to_A2_drift_percentage_change"], 0)
        self.assertGreater(c2["B_vs_A1_A2_center_percentage_change"], 100.0)
        self.assertNotIn("causal_speedup_verified", json.dumps(record))
        self.assertEqual(record["quality"]["samples"], 2)

    def test_path_reuse_rejected(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            aba_analysis.analyze(self.a1, self.b, self.a1)
        with self.assertRaisesRegex(ValueError, "distinct"):
            aba_analysis.analyze(self.a1, self.a1, self.a2)

    def test_cli_creates_an_aggregate_once(self):
        output = self.fixture.root / "analysis" / "aba.json"
        argv = ["--a1", str(self.a1), "--b", str(self.b),
                "--a2", str(self.a2), "--output", str(output)]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(aba_analysis.main(argv), 0)
        record = json.loads(output.read_text())
        self.assertEqual(record["status"], "exploratory_small_sample")
        original = output.read_text()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(aba_analysis.main(argv), 2)
        self.assertEqual(output.read_text(), original)

    def test_below_threshold_is_never_described_as_verified(self):
        record = aba_analysis.analyze(self.a1, self.b, self.a2)
        self.assertNotEqual(record["status"], "verified_causal_speedup")
        self.assertIn("causality", json.dumps(record["limitations"]))

    def test_incorrect_return_model_digest_fails_closed(self):
        record = json.loads(self.a2.read_text())
        record["model_digest"] = "d" * 64
        self.a2.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            aba_analysis.analyze(self.a1, self.b, self.a2)

    def test_incorrect_return_quality_dataset_fails_closed(self):
        file = self.a2_dir / "quality.json"
        item = json.loads(file.read_text())
        item["dataset_sha256"] = "d" * 64
        file.write_text(json.dumps(item))
        with self.assertRaisesRegex(ValueError, "quality dataset"):
            aba_analysis.analyze(self.a1, self.b, self.a2)

    def test_tampered_raw_return_p95_fails_closed(self):
        file = self.a2_dir / "repeat-01-c1.json"
        report = json.loads(file.read_text())
        report["results"]["latency_ms"]["p95"] = 98765
        file.write_text(json.dumps(report))
        with self.assertRaises(ValueError):
            aba_analysis.analyze(self.a1, self.b, self.a2)

    def test_tampered_return_summary_fails_closed(self):
        file = self.a2_dir / "sweep-summary.json"
        summary = json.loads(file.read_text())
        summary["summary"]["2"]["requests_per_second"]["median"] = 999
        file.write_text(json.dumps(summary))
        with self.assertRaisesRegex(ValueError, "summary disagrees"):
            aba_analysis.analyze(self.a1, self.b, self.a2)

    def test_return_quality_regression_flagged_not_a_success(self):
        self.fixture.write_bundle(self.a2_dir, wall=1.05, correct=1)
        record = aba_analysis.analyze(self.a1, self.b, self.a2)
        self.assertEqual(record["status"], "quality_regression_requires_review")
        self.assertFalse(record["quality"]["A2_no_observed_regression_from_A1"])

    def test_candidate_quality_regression_flagged(self):
        self.fixture.write_bundle(self.fixture.candidate, wall=0.5, correct=1)
        record = aba_analysis.analyze(self.a1, self.b, self.a2)
        self.assertEqual(record["status"], "quality_regression_requires_review")
        self.assertFalse(record["quality"]["B_no_observed_regression_from_A1"])

    def test_token_usage_gap_never_estimated(self):
        self.fixture.write_bundle(self.fixture.baseline, wall=1.0, completion_usage=False)
        self.fixture.write_bundle(self.fixture.candidate, wall=0.5, completion_usage=False)
        self.fixture.write_bundle(self.a2_dir, wall=1.05, completion_usage=False)
        record = aba_analysis.analyze(self.a1, self.b, self.a2)
        self.assertEqual(record["status"], "exploratory_small_sample")
        usage = record["metrics_by_concurrency"]["2"]["output_tokens_per_second"]
        self.assertIsNone(usage["A1_median"])
        self.assertIsNone(usage["B_vs_A1_A2_center_percentage_change"])

    def test_missing_intermediate_quality_file_rejected(self):
        (self.fixture.candidate / "quality.json").unlink()
        with self.assertRaises(ValueError):
            aba_analysis.analyze(self.a1, self.b, self.a2)

    def test_cli_bad_output_extension_fails_without_writing(self):
        output = self.fixture.root / "summary.txt"
        argv = ["--a1", str(self.a1), "--b", str(self.b),
                "--a2", str(self.a2), "--output", str(output)]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(aba_analysis.main(argv), 2)
        self.assertFalse(output.exists())

    def test_cli_rejects_overwriting_original_manifest(self):
        original = self.a1.read_text()
        argv = ["--a1", str(self.a1), "--b", str(self.b),
                "--a2", str(self.a2), "--output", str(self.a1)]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(aba_analysis.main(argv), 2)
        self.assertEqual(self.a1.read_text(), original)


if __name__ == "__main__":
    unittest.main()
