"""One-command portfolio contract demo: real Go gateway; no Ollama or GPU."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class OfflinePortfolioDemoTests(unittest.TestCase):
    def test_real_gateway_synthetic_demo_is_reproducible_and_sanitized(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.demo.offline"],
            cwd=ROOT, capture_output=True, text=True, timeout=100,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["demo_kind"], "synthetic_gateway_contract_demo")
        self.assertEqual(report["runtime"], "real_go_gateway_with_fake_local_model")
        self.assertIs(report["model_inference_executed"], False)
        self.assertIs(report["performance_measurement"], False)
        self.assertIs(report["passed"], True)
        self.assertEqual(report["checks_passed"], len(report["checks"]))
        self.assertEqual(report["checks_passed"], 15)
        self.assertEqual(len(set(report["checks"])), len(report["checks"]))
        self.assertEqual(report["admission"]["queued"], 2)
        self.assertEqual(report["admission"]["rejected_full"], 1)
        self.assertEqual(
            report["upstream_failures"], {"http_rejected": 1, "protocol": 1}
        )
        self.assertEqual(
            report["inference_outcomes"],
            {"attempts": 11, "nonstream_complete": 7,
             "stream_clean_eof": 2, "stream_done": 1,
             "stream_missing_done": 1, "pre_response_failure": 2},
        )
        for secret in (
            "PRIVATE_DEMO_PROMPT", "PRIVATE_FIXTURE_REJECTION",
            "DO_NOT_LEAK", "fixture-only reply",
        ):
            self.assertNotIn(secret, result.stdout)
            self.assertNotIn(secret, result.stderr)

    def test_missing_go_is_sanitized_and_nonzero(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.demo.offline",
             "--go", "nonexistent-go-command-for-demo-test-7286"],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("offline_demo_failed:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("PRIVATE_DEMO_PROMPT", result.stderr)
        self.assertFalse(result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
