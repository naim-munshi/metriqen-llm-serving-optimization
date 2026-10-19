"""Offline synthetic P1 evidence-review contracts, not real Mac acceptance."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.demo import p1_evidence_review as reviewer
from tools.demo import real_ollama

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
TOKEN = "dont-print-this-private-test-token"

SMOKE = {
    "schema_version": 1,
    "kind": "operator_local_real_model_smoke_observation",
    "timestamp_utc": "2026-10-19T00:34:00+00:00",
    "model_tag": "llama3.2:1b",
    "model_digest": DIGEST_A,
    "gateway": "127.0.0.1:8081",
    "passed": True,
    "model_inference_http_observed": True,
    "performance_benchmark": False,
    "actual_gpu_execution_verified": False,
    "tests_passed": sorted(reviewer.SMOKE_CHECKS),
}
LIFECYCLE = {
    "schema_version": 1,
    "kind": "operator_local_deployment_lifecycle_observation",
    "timestamp_utc": "2026-10-20T00:10:00Z",
    "deployment": "assistant",
    "original_tag": "llama3.2:1b",
    "original_digest": DIGEST_A,
    "candidate_tag": "llama3.2:latest",
    "candidate_digest": DIGEST_B,
    "passed": True,
    "executed_state_changes": True,
    "actual_model_execution_http_observed": True,
    "hardware_acceleration_verified": False,
    "performance_benchmark": False,
    "portfolio_p1_complete": False,
    "sequential_model_load": True,
    "checks_passed": sorted(reviewer.LIFECYCLE_CHECKS |
                            {"candidate_loaded_at_promotion",
                             "baseline_reloaded_before_rollback"}),
}


class EvidenceReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.patcher = patch.object(reviewer, "ROOT", root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.private = root / "reports/local"
        self.private.mkdir(parents=True)
        self.smoke = self.private / "smoke.json"
        self.life = self.private / "life.json"
        self.put(self.smoke, SMOKE)
        self.put(self.life, LIFECYCLE)

    def put(self, path, record):
        path.write_text(json.dumps(record))
        os.chmod(path, 0o600)

    def test_sanitized_consistency_review_does_not_mark_p1_complete(self):
        result = reviewer.review(self.smoke, self.life)
        self.assertTrue(result["p1_cli_consistency_checks_passed"])
        self.assertFalse(result["portfolio_p1_complete"])
        self.assertIn("browser_review_pending", result["classification"])
        self.assertEqual(result["model_tag"], "llama3.2:1b")
        self.assertEqual(result["model_digest_prefix"], "a" * 12)
        self.assertEqual(result["candidate_digest_prefix"], "b" * 12)
        self.assertTrue(result["sequential_model_load"])
        self.assertNotIn(DIGEST_A, json.dumps(result))
        self.assertNotIn(DIGEST_B, json.dumps(result))
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_missing_promote_or_rollback_blocked(self):
        for required in ("rollback_restores_original_inference",
                         "promoted_alias_routes_candidate_inference",
                         "baseline_reloaded_before_rollback"):
            with self.subTest(required=required):
                record = {**LIFECYCLE,
                          "checks_passed": [x for x in LIFECYCLE["checks_passed"] if x != required]}
                self.put(self.life, record)
                with self.assertRaisesRegex(reviewer.EvidenceError, "missing_or_duplicate"):
                    reviewer.review(self.smoke, self.life)
        self.put(self.life, LIFECYCLE)

    def test_smoke_missing_sse_or_real_http_is_not_eligible(self):
        record = {**SMOKE, "tests_passed":
                  [x for x in SMOKE["tests_passed"] if x != "gateway_streaming_delta_and_done"]}
        self.put(self.smoke, record)
        with self.assertRaisesRegex(reviewer.EvidenceError, "missing_or_duplicate"):
            reviewer.review(self.smoke, self.life)
        record["tests_passed"] = SMOKE["tests_passed"]
        record["model_inference_http_observed"] = False
        self.put(self.smoke, record)
        with self.assertRaisesRegex(reviewer.EvidenceError, "smoke_observation_not_eligible"):
            reviewer.review(self.smoke, self.life)

    def test_mismatched_model_digest_or_missing_candidate_blocked(self):
        self.put(self.life, {**LIFECYCLE, "original_digest": "f" * 64})
        with self.assertRaisesRegex(reviewer.EvidenceError, "digest_or_tag_mismatch"):
            reviewer.review(self.smoke, self.life)
        self.put(self.life, {**LIFECYCLE, "candidate_tag": None})
        with self.assertRaisesRegex(reviewer.EvidenceError, "candidate_revision_missing"):
            reviewer.review(self.smoke, self.life)

    def test_stop_start_only_report_cannot_claim_rollback(self):
        self.put(self.life, {**LIFECYCLE, "candidate_tag": None,
                             "candidate_digest": None,
                             "sequential_model_load": False,
                             "checks_passed": sorted(reviewer.LIFECYCLE_CHECKS -
                                {"promoted_alias_routes_candidate_inference",
                                 "rollback_restores_original_inference"})})
        with self.assertRaisesRegex(reviewer.EvidenceError, "candidate_revision_missing"):
            reviewer.review(self.smoke, self.life)

    def test_duplicate_json_fields_or_symlink_are_rejected(self):
        self.smoke.write_text('{"schema_version":1,"schema_version":1}')
        os.chmod(self.smoke, 0o600)
        with self.assertRaisesRegex(reviewer.EvidenceError, "duplicate_json_field"):
            reviewer.review(self.smoke, self.life)
        self.put(self.smoke, SMOKE)
        symlink = self.private / "linked.json"
        symlink.symlink_to(self.smoke)
        with self.assertRaisesRegex(reviewer.EvidenceError, "evidence_must_be_private"):
            reviewer.review(symlink, self.life)

    def test_oversized_or_public_permission_reports_rejected(self):
        os.chmod(self.life, 0o644)
        with self.assertRaisesRegex(reviewer.EvidenceError, "permissions_or_size"):
            reviewer.review(self.smoke, self.life)
        self.life.write_bytes(b" " * 66000)
        os.chmod(self.life, 0o600)
        with self.assertRaisesRegex(reviewer.EvidenceError, "permissions_or_size"):
            reviewer.review(self.smoke, self.life)

    def test_private_sanitized_cli_output_is_exclusive(self):
        out = self.private / "summary.json"
        with patch.object(real_ollama, "ROOT", reviewer.ROOT):
            with redirect_stdout(StringIO()) as stdout:
                self.assertEqual(0, reviewer.main(["--smoke", str(self.smoke),
                    "--lifecycle", str(self.life), "--output", str(out)]))
            self.assertIn("NOT YET COMPLETE", stdout.getvalue())
            self.assertNotIn(TOKEN, stdout.getvalue())
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(DIGEST_A, out.read_text())
            self.assertNotIn(TOKEN, out.read_text())
            with redirect_stderr(StringIO()):
                self.assertEqual(2, reviewer.main(["--smoke", str(self.smoke),
                    "--lifecycle", str(self.life), "--output", str(out)]))

    def test_bad_timestamp_cannot_be_silently_accepted(self):
        self.put(self.smoke, {**SMOKE, "timestamp_utc": "2026-10-19T00:34:00"})
        with self.assertRaisesRegex(reviewer.EvidenceError, "timestamp_timezone_required"):
            reviewer.review(self.smoke, self.life)

    def test_reports_must_be_distinct(self):
        with self.assertRaisesRegex(reviewer.EvidenceError, "reports_must_be_distinct"):
            reviewer.review(self.smoke, self.smoke)


if __name__ == "__main__":
    unittest.main()
