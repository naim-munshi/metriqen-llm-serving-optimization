"""Synthetic offline run-level variability reviewer contract tests."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.benchmark import run_review, sweep
from tools.benchmark.tests import test_sweep


class RunReviewTests(unittest.TestCase):
    def setUp(self):
        fixture = test_sweep.SweepTests(methodName="test_rotating_order_is_balanced")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        with patch.object(sweep.runner, "main", side_effect=fixture.write_fixture):
            with redirect_stdout(StringIO()):
                self.summary = sweep.run(fixture.args)
        self.output = fixture.root / "review.md"

    def test_rechecks_raw_runs_and_preserves_exact_order(self):
        report = run_review.review(self.fixture.args.output_dir)
        self.assertEqual(report["repeats"], 3)
        self.assertEqual(len(report["rows"]), 9)
        self.assertEqual([row["concurrency"] for row in report["rows"]],
                         [1, 2, 4, 2, 4, 1, 4, 1, 2])
        self.assertFalse(report["has_timing"])
        md = run_review.render(report)
        self.assertIn("Not recorded", md)
        self.assertIn("descriptive screening only", md)
        self.assertNotIn("PRIVATE_PROMPT", md)
        self.assertNotIn(str(self.fixture.root), md)
        self.assertNotIn("speedup verified", md)

    def test_detects_large_repeat_spread_without_deleting_outlier(self):
        folder = self.fixture.args.output_dir
        path = folder / "repeat-03-c2.json"
        report = json.loads(path.read_text())
        report["results"]["requests_per_second"] = 0.8
        report["results"]["wall_seconds"] = 2.5
        report["results"]["output_tokens_per_second"] = 2.4
        path.write_text(json.dumps(report))
        summary = json.loads(self.summary.read_text())
        summary["summary"]["2"]["requests_per_second"]["max"] = 0.8
        summary["summary"]["2"]["requests_per_second"]["median"] = 0.2
        summary["summary"]["2"]["output_tokens_per_second"]["max"] = 2.4
        summary["summary"]["2"]["output_tokens_per_second"]["median"] = 0.6
        self.summary.write_text(json.dumps(summary))
        result = run_review.review(folder)
        self.assertEqual(result["screening"]["2"]["throughput_max_min_ratio"], 4)
        self.assertTrue(result["screening"]["2"]["output_tokens_equal_between_repeats"])
        self.assertTrue(any("4.00x" in reason for reason in result["warnings"]))
        self.assertEqual(len(result["rows"]), 9)

    def test_tampered_raw_request_rate_rejected(self):
        target = self.fixture.args.output_dir / "repeat-02-c2.json"
        raw = json.loads(target.read_text())
        raw["results"]["requests_per_second"] = 999.0
        target.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError, "published sweep summary"):
            run_review.review(self.fixture.args.output_dir)

    def test_mismatched_rotating_order_rejected(self):
        summary = json.loads(self.summary.read_text())
        summary["run_files"][0], summary["run_files"][1] = summary["run_files"][1], summary["run_files"][0]
        self.summary.write_text(json.dumps(summary))
        with self.assertRaisesRegex(ValueError, "rotating protocol"):
            run_review.review(self.fixture.args.output_dir)

    def test_cli_writes_once_and_refuses_overwrite(self):
        argv = ["--sweep-dir", str(self.fixture.args.output_dir), "--output", str(self.output)]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(run_review.main(argv), 0)
        baseline = self.output.read_text()
        self.assertIn("Repeat variability", baseline)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(run_review.main(argv), 2)
        self.assertEqual(self.output.read_text(), baseline)


if __name__ == "__main__":
    unittest.main()
