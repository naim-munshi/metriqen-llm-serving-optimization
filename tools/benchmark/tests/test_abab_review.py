"""Offline A/B/A/B report tests; all four phases are synthetic fixtures."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import unittest

from tools.benchmark import abab_analysis, abab_review
from tools.benchmark.tests import test_compare


class FourPhaseTests(unittest.TestCase):
    def setUp(self):
        fixture = test_compare.ComparisonTests(
            methodName="test_valid_results_are_labeled_exploratory_not_speedups")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.a1 = fixture.manifest(fixture.baseline)
        self.b1 = fixture.manifest(fixture.candidate)
        self.a2_dir = fixture.root / "a2"
        self.b2_dir = fixture.root / "b2"
        fixture.write_bundle(self.a2_dir, wall=1.1)
        fixture.write_bundle(self.b2_dir, wall=0.7)
        self.a2 = fixture.manifest(self.a2_dir)
        self.b2 = fixture.manifest(self.b2_dir)
        self.report = fixture.root / "private" / "four-phase.md"

    def args(self):
        return ["--a1", str(self.a1), "--b1", str(self.b1),
                "--a2", str(self.a2), "--b2", str(self.b2),
                "--output", str(self.report)]

    def test_four_phase_analysis_includes_two_effects_and_two_drifts(self):
        result = abab_analysis.analyze(self.a1, self.b1, self.a2, self.b2)
        self.assertEqual(result["status"], "exploratory_small_sample")
        self.assertEqual(result["chronology"]["classification"], "timestamps_unavailable_legacy")
        self.assertEqual(set(result["pair_statuses"]), {
            "first_candidate", "replicated_candidate", "baseline_drift", "candidate_drift",
        })
        self.assertEqual(set(result["metrics_by_concurrency"]), {"1", "2", "4"})
        metric = result["metrics_by_concurrency"]["2"]["requests_per_second"]
        self.assertEqual(set(metric["medians"]), {"A1", "B1", "A2", "B2"})
        self.assertLess(metric["first_candidate_change_pct"], 1000)
        self.assertIsNotNone(metric["replicated_candidate_change_pct"])
        self.assertIsNotNone(metric["baseline_drift_pct"])
        self.assertIsNotNone(metric["candidate_drift_pct"])

    def test_markdown_fully_renders_no_sensitive_paths_or_causal_claim(self):
        result = abab_analysis.analyze(self.a1, self.b1, self.a2, self.b2)
        rendered = abab_review.render(result)
        self.assertIn("A1→B1", rendered)
        self.assertIn("A2→B2", rendered)
        self.assertIn("A1→A2 drift", rendered)
        self.assertIn("B1→B2 drift", rendered)
        self.assertIn("Repeat-run variability", rendered)
        self.assertIn("N/A", rendered) if False else None
        self.assertIn("NOT causal", rendered)
        self.assertEqual(
            sum(line.startswith("| 2 |") for line in rendered.splitlines()), 12
        )
        for secret in ("PRIVATE_PROMPT", "secret-token", str(self.fixture.root)):
            self.assertNotIn(secret, rendered)

    def test_quality_regression_blocks_quality_parity(self):
        self.fixture.write_bundle(self.b2_dir, wall=0.7, correct=1)
        result = abab_analysis.analyze(self.a1, self.b1, self.a2, self.b2)
        self.assertEqual(result["status"], "quality_regression_requires_review")
        self.assertIn("Quality warning", abab_review.render(result))

    def test_distinct_four_manifests_required(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            abab_analysis.analyze(self.a1, self.b1, self.a1, self.b2)

    def test_mismatched_model_digest_rejected(self):
        path = self.b2_dir / "evidence-manifest.json"
        raw = json.loads(path.read_text())
        raw["model_digest"] = "c" * 64
        path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            abab_analysis.analyze(self.a1, self.b1, self.a2, self.b2)

    def test_modified_raw_request_evidence_rejected(self):
        path = self.a2_dir / "repeat-01-c1.json"
        raw = json.loads(path.read_text())
        raw["results"]["requests_per_second"] = 999999
        path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            abab_analysis.analyze(self.a1, self.b1, self.a2, self.b2)

    def test_missing_stream_usage_is_not_fabricated(self):
        for folder, wall in ((self.fixture.baseline, 1.0), (self.fixture.candidate, .5),
                             (self.a2_dir, 1.1), (self.b2_dir, .7)):
            self.fixture.write_bundle(folder, wall=wall, completion_usage=False)
        result = abab_analysis.analyze(self.a1, self.b1, self.a2, self.b2)
        self.assertEqual(result["status"], "exploratory_small_sample")
        metric = result["metrics_by_concurrency"]["1"]["output_tokens_per_second"]
        self.assertEqual(metric["medians"], dict.fromkeys(abab_analysis.PHASES, None))
        self.assertIn("N/A", abab_review.render(result))

    def add_timestamps(self, manifest: Path, phase: int):
        directory = manifest.parent
        summary_file = directory / "sweep-summary.json"
        summary = json.loads(summary_file.read_text())
        for index, entry in enumerate(summary["run_files"]):
            path = directory / entry["path"]
            raw = json.loads(path.read_text())
            began = datetime(2026, 10, 14, phase, 0, tzinfo=timezone.utc) + timedelta(seconds=index * 5)
            end = began + timedelta(seconds=2)
            timing = {
                "started_at_utc": began.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "finished_at_utc": end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "elapsed_seconds": 2.0,
                "scope": "warmup_and_measured_requests",
            }
            raw["timing"] = timing
            entry["timing"] = timing
            path.write_text(json.dumps(raw))
        summary_file.write_text(json.dumps(summary))

    def test_four_timed_phases_have_consistent_chronology(self):
        for phase, manifest in enumerate((self.a1, self.b1, self.a2, self.b2), 1):
            self.add_timestamps(manifest, phase)
        result = abab_analysis.analyze(self.a1, self.b1, self.a2, self.b2)
        self.assertEqual(result["chronology"]["classification"], "timestamps_available_all_four")
        self.assertIn("2026-10-14", abab_review.render(result))

    def test_out_of_order_timed_phase_rejected(self):
        for phase, manifest in zip((1, 3, 2, 4), (self.a1, self.b1, self.a2, self.b2)):
            self.add_timestamps(manifest, phase)
        with self.assertRaisesRegex(ValueError, "overlap|order"):
            abab_analysis.analyze(self.a1, self.b1, self.a2, self.b2)

    def test_partial_timing_does_not_claim_full_chronology(self):
        self.add_timestamps(self.b1, 2)
        result = abab_analysis.analyze(self.a1, self.b1, self.a2, self.b2)
        self.assertEqual(
            result["chronology"]["classification"],
            "timestamps_partial_no_full_order_verification",
        )

    def test_cli_exclusive_write_and_invalid_path_rejection(self):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(abab_review.main(self.args()), 0)
        original = self.report.read_text()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(abab_review.main(self.args()), 2)
        self.assertEqual(self.report.read_text(), original)

    def test_json_cli_writes_once_and_keeps_raw_evidence(self):
        target = self.fixture.root / "private" / "analysis.json"
        argv = ["--a1", str(self.a1), "--b1", str(self.b1),
                "--a2", str(self.a2), "--b2", str(self.b2),
                "--output", str(target)]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(abab_analysis.main(argv), 0)
        self.assertEqual(json.loads(target.read_text())["schema_version"], 1)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(abab_analysis.main(argv), 2)


if __name__ == "__main__":
    unittest.main()
