"""Offline reviewer-report tests use synthetic benchmark fixtures, not inference."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest

from tools.benchmark import aba_analysis, aba_review
from tools.benchmark.tests import test_compare


class ABAReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = test_compare.ComparisonTests(
            methodName="test_valid_results_are_labeled_exploratory_not_speedups"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.a1 = fixture.manifest(fixture.baseline)
        self.b = fixture.manifest(fixture.candidate)
        self.a2_dir = fixture.root / "return"
        fixture.write_bundle(self.a2_dir, wall=1.05)
        self.a2 = fixture.manifest(self.a2_dir)
        self.output = fixture.root / "local" / "review.md"

    def _argv(self):
        return ["--a1", str(self.a1), "--b", str(self.b),
                "--a2", str(self.a2), "--output", str(self.output)]

    def _call(self):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return aba_review.main(self._argv())

    def test_three_phases_all_six_metrics_and_drift_rendered(self):
        result = aba_analysis.analyze(self.a1, self.b, self.a2)
        report = aba_review.render(result)
        self.assertIn("A1 median | B median | A2 median", report)
        self.assertIn("A1→A2 drift", report)
        self.assertIn("B vs A midpoint", report)
        for concurrency in ("1", "2", "4"):
            self.assertEqual(
                sum(line.startswith(f"| {concurrency} |") for line in report.splitlines()),
                6
            )
        self.assertIn("Successful requests/sec", report)
        self.assertIn("TTFT p95", report)
        self.assertIn("100.00%", report)
        self.assertIn("NOT proof of a causal speedup", report)

    def test_quality_scores_include_all_three_phases(self):
        result = aba_analysis.analyze(self.a1, self.b, self.a2)
        md = aba_review.render(result)
        for label in ("A1", "B", "A2"):
            self.assertIn(f"| {label} | 100.00% | 100.00% |", md)
        self.assertIn("reference samples per phase", md)

    def test_no_raw_prompts_paths_or_response_content_in_export(self):
        result = aba_analysis.analyze(self.a1, self.b, self.a2)
        md = aba_review.render(result)
        for private in ("PRIVATE_PROMPT", str(self.fixture.root), "secret-token", "response_content"):
            self.assertNotIn(private, md)

    def test_cli_creates_once_and_refuses_overwrite(self):
        self.assertEqual(self._call(), 0)
        original = self.output.read_text()
        self.assertIn("exploratory only", original)
        self.assertEqual(self._call(), 2)
        self.assertEqual(self.output.read_text(), original)

    def test_tampered_raw_return_report_never_written(self):
        file = self.a2_dir / "repeat-01-c1.json"
        report = json.loads(file.read_text())
        report["results"]["latency_ms"]["p95"] = 999999
        file.write_text(json.dumps(report))
        self.assertEqual(self._call(), 2)
        self.assertFalse(self.output.exists())

    def test_quality_regression_never_silently_classified_as_safe(self):
        self.fixture.write_bundle(self.a2_dir, wall=1.05, correct=1)
        report = aba_review.render(aba_analysis.analyze(self.a1, self.b, self.a2))
        self.assertIn("quality_regression_requires_review", report)
        self.assertIn("Quality warning", report)

    def test_missing_usage_never_fabricated(self):
        self.fixture.write_bundle(self.fixture.baseline, wall=1, completion_usage=False)
        self.fixture.write_bundle(self.fixture.candidate, wall=0.5, completion_usage=False)
        self.fixture.write_bundle(self.a2_dir, wall=1.05, completion_usage=False)
        report = aba_review.render(aba_analysis.analyze(self.a1, self.b, self.a2))
        self.assertIn("Output tokens/sec (tokens/s) | N/A | N/A | N/A", report)

    def test_bad_file_extension_rejected(self):
        self.output = self.output.with_suffix(".json")
        self.assertEqual(self._call(), 2)
        self.assertFalse(self.output.exists())

    def test_same_phase_path_rejected(self):
        self.a2 = self.a1
        self.assertEqual(self._call(), 2)
        self.assertFalse(self.output.exists())

    def test_schema_rejected(self):
        result = aba_analysis.analyze(self.a1, self.b, self.a2)
        result["schema_version"] = 999
        with self.assertRaisesRegex(ValueError, "unexpected"):
            aba_review.render(result)


if __name__ == "__main__":
    unittest.main()
