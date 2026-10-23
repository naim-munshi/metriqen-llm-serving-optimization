"""Never let synthetic/source-only checks manufacture P1–P6 approval."""
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from tools.demo import portfolio_readiness as audit


class ReadinessAuditTests(unittest.TestCase):
    def test_current_project_is_explicitly_blocked_with_six_manual_gates(self):
        doc = audit.collect_source_status()
        self.assertEqual([g["id"] for g in doc["gates"]],
                         ["P1", "P2", "P3", "P4", "P5", "P6"])
        self.assertTrue(doc["docs_present"])
        self.assertFalse(doc["release_approved"])
        self.assertFalse(doc["portfolio_v1_released"])
        self.assertFalse(doc["gpu_autoscaling_deployed"])
        self.assertFalse(doc["public_https_deployed"])
        self.assertFalse(doc["real_google_oauth_verified"])
        self.assertFalse(doc["operator_mac_execution_verified"])
        self.assertEqual(set(g["status"] for g in doc["gates"]),
                         {"manual_acceptance_required"})
        self.assertIn("BLOCKED", doc["conclusion"])

    def test_missing_docs_cannot_make_any_gate_pass(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            doc = audit.collect_source_status(root)
            self.assertFalse(doc["docs_present"])
            self.assertFalse(doc["owner_licensing_decision_file_present"])
            self.assertTrue(all(g["status"] == "manual_acceptance_required"
                                for g in doc["gates"]))
            for name in audit.PUBLIC_DOCS:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic document", encoding="utf-8")
            self.assertTrue(audit.collect_source_status(root)["docs_present"])
            license_file = root / "LICENSE"
            license_file.write_text("synthetic placeholder", encoding="utf-8")
            doc = audit.collect_source_status(root)
            self.assertTrue(doc["owner_licensing_decision_file_present"])
            self.assertFalse(doc["release_approved"])
            self.assertFalse(doc["portfolio_v1_released"])
            self.assertTrue(all(g["status"] == "manual_acceptance_required"
                                for g in doc["gates"]))

    def test_sanitized_json_is_machine_readable_but_returns_blocked(self):
        output = StringIO()
        with redirect_stdout(output):
            exit_code = audit.main(["--json"])
        self.assertEqual(exit_code, 2)
        text = output.getvalue()
        result = json.loads(text)
        self.assertEqual(result["schema"], "metriqen.portfolio-readiness.v1")
        self.assertEqual(result["scope"], "source-only-offline")
        self.assertFalse(result["release_approved"])
        for secret in ("Authorization:", "Bearer eyJ", "client_secret=",
                       "refresh_token=", "private_key=", "/Users/mdnaim"):
            self.assertNotIn(secret, text)
        self.assertNotIn("PASS", str(result["gates"]))

    def test_human_output_also_returns_blocked(self):
        output = StringIO()
        with redirect_stdout(output):
            exit_code = audit.main([])
        self.assertEqual(exit_code, 2)
        self.assertIn("BLOCKED", output.getvalue())
        self.assertEqual(output.getvalue().count("MANUAL REVIEW REQUIRED"), 6)
        self.assertIn("No Mac, browser, Google OAuth", output.getvalue())


if __name__ == "__main__":
    unittest.main()
