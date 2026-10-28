"""Synthetic-only tests: no Ollama/Gateway is contacted or stopped."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools.demo import local_readonly_review as local
from tools.demo import p5_readonly_recovery_review as p5


def observation(at: datetime, phase: str) -> dict:
    checks = []
    for name, _url, _kind, required in local.PROBES:
        if name == "account_console_page":
            status, code = "optional_unavailable", 404
        elif name == "model_manager_unauthenticated":
            status, code = "pass", 401
        else:
            status, code = "pass", 200
        if phase == "outage" and name == "gateway_upstream":
            status, code = "fail", 503
        if phase == "outage" and name == "ollama_version":
            status, code = "fail", None
        checks.append({
            "check": name, "status": status,
            "http_status": code, "required": required,
        })
    return {
        "schema": "metriqen.local-readonly-review.v1",
        "scope": "operator_requested_local_get_only",
        "observed_at_utc": at.isoformat(timespec="seconds"),
        "checks": checks,
        "required_checks_passed": phase != "outage",
        "oauth_signin_verified": False,
        "real_model_inference_verified": False,
        "browser_visual_acceptance_verified": False,
        "mac_network_listener_binding_verified": False,
        "portfolio_p4_or_p5_approved": False,
        "notes": ["SYNTHETIC FIXTURE, NOT MAC EVIDENCE"],
    }


class RecoveryReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "reports" / "local"
        self.root.mkdir(parents=True, mode=0o700)
        self.root.chmod(0o700)
        mocked = patch.object(local, "REPORT_ROOT", self.root)
        mocked.start()
        self.addCleanup(mocked.stop)
        now = datetime(2026, 10, 28, 2, tzinfo=timezone.utc)
        self.docs = [
            observation(now, "baseline"),
            observation(now + timedelta(minutes=3), "outage"),
            observation(now + timedelta(minutes=9), "recovered"),
        ]
        self.paths = [self.root / f"stage-{i}.json" for i in range(3)]
        self._write_all()

    def _write_all(self):
        for path, doc in zip(self.paths, self.docs):
            path.write_text(json.dumps(doc), encoding="utf-8")
            path.chmod(0o600)

    def test_default_is_plan_only_and_has_zero_io(self):
        with patch.object(p5, "review", side_effect=AssertionError("unexpected read")):
            buf = StringIO()
            with redirect_stdout(buf):
                self.assertEqual(p5.main([]), 0)
        self.assertIn("PLAN ONLY", buf.getvalue())

    def test_healthy_unavailable_healthy_reports_produce_only_narrow_claims(self):
        result = p5.review(*self.paths)
        self.assertTrue(result["observed_readonly_health_transition"])
        self.assertEqual(len(result["checks_passed"]), 5)
        for name in (
            "gateway_process_continuity_verified",
            "failed_inference_response_verified",
            "successful_inference_after_recovery_verified",
            "real_browser_recovery_verified",
            "p5_reliability_security_gate_complete",
        ):
            self.assertFalse(result[name])
        text = json.dumps(result)
        self.assertNotIn("SYNTHETIC FIXTURE", text)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("token", text.lower())

    def test_cli_saves_private_report_but_never_signs_off_p5(self):
        target = self.root / "review.json"
        args = ["--baseline", str(self.paths[0]), "--outage", str(self.paths[1]),
                "--recovered", str(self.paths[2]), "--output", str(target)]
        buf = StringIO()
        with redirect_stdout(buf):
            self.assertEqual(p5.main(args), 0)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertIn("NOT YET COMPLETE", buf.getvalue())
        # Never overwrite the existing report on a repeated run.
        with redirect_stderr(StringIO()):
            self.assertEqual(p5.main(args), 2)

    def test_reversed_or_equal_timestamps_fail_closed(self):
        self.docs[2]["observed_at_utc"] = self.docs[1]["observed_at_utc"]
        self._write_all()
        with self.assertRaisesRegex(p5.ReviewBlocked, "observation_order_invalid"):
            p5.review(*self.paths)

    def test_outage_without_503_or_gateway_live_is_rejected(self):
        for key, value in (("gateway_upstream", 200), ("gateway_live", 503),
                           ("gateway_console", 503)):
            with self.subTest(key=key):
                doc = json.loads(json.dumps(self.docs[1]))
                item = next(x for x in doc["checks"] if x["check"] == key)
                item["http_status"] = value
                if key != "gateway_upstream":
                    item["status"] = "fail"
                self.docs[1] = doc
                self._write_all()
                with self.assertRaisesRegex(p5.ReviewBlocked, "unavailable_upstream_transition_missing"):
                    p5.review(*self.paths)
                self.docs[1] = observation(
                    datetime(2026, 10, 28, 2, 3, tzinfo=timezone.utc), "outage"
                )

    def test_direct_ollama_responding_during_outage_does_not_pass(self):
        item = next(x for x in self.docs[1]["checks"] if x["check"] == "ollama_version")
        item.update(status="pass", http_status=200)
        self._write_all()
        with self.assertRaisesRegex(p5.ReviewBlocked, "unavailable_upstream_transition_missing"):
            p5.review(*self.paths)

    def test_missing_required_healthy_or_anonymous_registry_401_fails(self):
        for check_name in ("gateway_upstream", "model_manager_unauthenticated", "gateway_live"):
            with self.subTest(check=check_name):
                original = json.loads(json.dumps(self.docs[2]))
                item = next(x for x in self.docs[2]["checks"] if x["check"] == check_name)
                item["http_status"] = 500
                item["status"] = "fail"
                self._write_all()
                with self.assertRaisesRegex(p5.ReviewBlocked, "healthy_boundary_missing"):
                    p5.review(*self.paths)
                self.docs[2] = original

    def test_duplicate_keys_and_checks_are_rejected(self):
        self.paths[0].write_text('{"schema":1,"schema":2}', encoding="utf-8")
        with self.assertRaisesRegex(p5.ReviewBlocked, "duplicate_json_key"):
            p5.review(*self.paths)
        self._write_all()
        self.docs[0]["checks"][1]["check"] = self.docs[0]["checks"][0]["check"]
        self._write_all()
        with self.assertRaisesRegex(p5.ReviewBlocked, "source_check_invalid"):
            p5.review(*self.paths)

    def test_false_scope_or_bogus_positive_security_flag_rejected(self):
        self.docs[0]["real_model_inference_verified"] = True
        self._write_all()
        with self.assertRaisesRegex(p5.ReviewBlocked, "unexpected_source_report_contract"):
            p5.review(*self.paths)

    def test_sources_refuse_outside_path_symlinks_open_permissions_and_duplicates(self):
        with self.assertRaisesRegex(p5.ReviewBlocked, "source_reports_must_be_distinct"):
            p5.review(self.paths[0], self.paths[0], self.paths[2])
        outside = self.root.parent / "elsewhere.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(p5.ReviewBlocked, "evidence_path_not_private_local_json"):
            p5.review(outside, self.paths[1], self.paths[2])
        self.paths[0].chmod(0o644)
        with self.assertRaisesRegex(p5.ReviewBlocked, "evidence_permissions_or_size_invalid"):
            p5.review(*self.paths)
        self.paths[0].unlink()
        self.paths[0].symlink_to(self.paths[1])
        with self.assertRaisesRegex(p5.ReviewBlocked, "evidence_path_not_private_local_json"):
            p5.review(*self.paths)

    def test_missing_arguments_output_overlap_and_unsafe_output_rejected(self):
        with redirect_stderr(StringIO()):
            self.assertEqual(p5.main(["--baseline", str(self.paths[0])]), 2)
        paths = ["--baseline", str(self.paths[0]), "--outage", str(self.paths[1]),
                 "--recovered", str(self.paths[2])]
        with redirect_stderr(StringIO()):
            self.assertEqual(p5.main(paths + ["--output", str(self.paths[1])]), 2)
            self.assertEqual(p5.main(paths + ["--output", str(self.root.parent / "other.json")]), 2)


if __name__ == "__main__":
    unittest.main()
