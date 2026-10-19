"""Contract tests for offline P2 aggregate assembly. Synthetic only."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.benchmark import four_phase_review as final


class FourPhaseOperatorReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.root = root
        self.private = root / "reports/local"
        self.private.mkdir(parents=True)
        replace = patch.object(final.phase_run, "PRIVATE", self.private)
        replace.start()
        self.addCleanup(replace.stop)
        self.dirs = {}
        for phase in final.PHASES:
            location = self.private / ("abab-" + phase)
            (location / "sweep").mkdir(parents=True)
            (location / "preflight.json").write_text("{}")
            (location / "sweep/evidence-manifest.json").write_text("{}")
            self.dirs[phase] = location
        self.output = self.private / "final-review"

    def test_offline_validation_before_private_write_and_no_false_completion(self):
        bind = {"classification": "operator_sourced_preflight_consistency_only",
                "phases": {phase: {"configuration_consistent": True}
                           for phase in final.PHASES}}
        analysis = {"status": "descriptive_replication_only",
                    "limitations": ["not causal or attested"]}
        with patch.object(final.abab_preflight_gate, "validate", return_value=bind) as gate, \
             patch.object(final.abab_analysis, "analyze", return_value=analysis) as analyzer, \
             patch.object(final.abab_review, "render", return_value="## Private actual data\n") as renderer, \
             redirect_stdout(StringIO()) as out:
            final.finalize(self.dirs, self.output)
        self.assertIn("NOT ATTESTED", out.getvalue())
        self.assertIn("human slot", out.getvalue())
        gate.assert_called_once()
        analyzer.assert_called_once()
        renderer.assert_called_once_with(analysis)
        self.assertEqual((self.output / "abab-preflight-binding.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.output / "abab-analysis.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.output / "abab-review.md").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertFalse(json.loads((self.output / "abab-preflight-binding.json").read_text()).get("portfolio_p2_complete", False))

    def test_failed_binding_creates_no_output_and_never_renders(self):
        with patch.object(final.abab_preflight_gate, "validate",
                          side_effect=final.abab_preflight_gate.PreflightEvidenceError("synthetic only")), \
             patch.object(final.abab_analysis, "analyze") as analysis:
            with self.assertRaises(final.abab_preflight_gate.PreflightEvidenceError):
                final.finalize(self.dirs, self.output)
        self.assertFalse(self.output.exists())
        analysis.assert_not_called()

    def test_missing_phase_data_rejected_before_comparison(self):
        (self.dirs["B2"] / "sweep/evidence-manifest.json").unlink()
        with patch.object(final.abab_preflight_gate, "validate") as gate:
            with self.assertRaisesRegex(final.ReviewError, "missing"):
                final.finalize(self.dirs, self.output)
        gate.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_duplicate_phase_directory_and_wrong_root_rejected(self):
        duplicate = {**self.dirs, "B2": self.dirs["B1"]}
        with self.assertRaisesRegex(final.ReviewError, "distinct"):
            final.finalize(duplicate, self.output)
        outside = self.root / "outside"
        (outside / "sweep").mkdir(parents=True)
        (outside / "preflight.json").write_text("{}")
        (outside / "sweep/evidence-manifest.json").write_text("{}")
        with self.assertRaisesRegex(final.ReviewError, "outside_private_root"):
            final.finalize({**self.dirs, "B2": outside}, self.output)

    def test_symlink_phase_input_and_existing_output_are_rejected(self):
        linked = self.private / "phase-link"
        linked.symlink_to(self.dirs["B1"], target_is_directory=True)
        with self.assertRaisesRegex(final.ReviewError, "unsafe"):
            final.finalize({**self.dirs, "B1": linked}, self.output)
        self.output.mkdir()
        with self.assertRaisesRegex(final.ReviewError, "must_be_new"):
            final.finalize(self.dirs, self.output)

    def test_no_sanitized_result_is_published_outside_private_directory(self):
        with self.assertRaisesRegex(final.ReviewError, "must_be_new"):
            final.finalize(self.dirs, self.root / "public-results")

    def test_cli_rejects_incomplete_input_without_false_pass(self):
        (self.dirs["A2"] / "preflight.json").unlink()
        args = []
        for phase in final.PHASES:
            args += ["--" + phase.lower() + "-dir", str(self.dirs[phase])]
        args += ["--output-dir", str(self.output)]
        with redirect_stderr(StringIO()) as err, redirect_stdout(StringIO()) as out:
            code = final.main(args)
        self.assertEqual(code, 2)
        self.assertIn("blocked", err.getvalue())
        self.assertEqual(out.getvalue(), "")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
