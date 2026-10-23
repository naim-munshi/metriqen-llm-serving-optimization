"""No-network contracts for operator Mac native-vs-gateway diagnostic."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from tools.benchmark import gateway_overhead as probe


class GatewayOverheadDiagnosticSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / "reports" / "local"
        self.base.mkdir(parents=True, mode=0o700)
        self.base.chmod(0o700)
        self.folder = self.base / "operator-test"
        self.patcher = patch.object(probe, "REPORT_ROOT", self.base)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_plan_is_exact_12_phase_abba_rotation(self):
        order = probe.plan()
        self.assertEqual(len(order), 12)
        for i, c in enumerate((1, 2, 4)):
            self.assertEqual(order[i*4:(i+1)*4],
                             ((c, 1, "native"), (c, 2, "gateway"),
                              (c, 3, "gateway"), (c, 4, "native")))
        self.assertEqual(set(probe.ENDPOINTS), {"native", "gateway"})
        self.assertEqual(probe.ENDPOINTS["gateway"],
                         "http://127.0.0.1:8081/v1/chat/completions")
        self.assertEqual(probe.ENDPOINTS["native"],
                         "http://127.0.0.1:11434/v1/chat/completions")

    def test_default_cli_does_not_contact_network_or_create_files(self):
        with patch.object(probe, "run_local", side_effect=AssertionError("executed")):
            with redirect_stdout(StringIO()) as out:
                self.assertEqual(probe.main([]), 0)
        self.assertIn("PLAN ONLY", out.getvalue())
        self.assertIn("no network/model calls", out.getvalue())
        self.assertEqual(list(self.base.iterdir()), [])

    def test_private_output_rules_refuse_bad_digest_and_paths(self):
        digest = "b" * 64
        self.assertEqual(probe.config_validation(
            "llama3.2:1b", digest, 12, 64, self.folder), self.folder)
        for model, d, n, tokens, folder in [
            ("llama3.2:1b", "fake-digest", 12, 64, self.folder),
            ("@bad-model", digest, 12, 64, self.folder),
            ("llama3.2:1b", digest, 0, 64, self.folder),
            ("llama3.2:1b", digest, 12, 129, self.folder),
            ("llama3.2:1b", digest, 12, 64, self.base),
            ("llama3.2:1b", digest, 12, 64, Path(self.tmp.name)/"elsewhere"),
            ("llama3.2:1b", digest, 12, 64, self.base/".."/"outside"),
        ]:
            with self.subTest(folder=folder, model=model, digest=d[:8]):
                with self.assertRaises(ValueError):
                    probe.config_validation(model, d, n, tokens, folder)
        self.folder.mkdir()
        with self.assertRaises(ValueError):
            probe.config_validation("llama3.2:1b", digest, 12, 64, self.folder)

    def test_preflight_fails_closed_with_wrong_ac_or_model(self):
        with patch.object(probe.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(ValueError, "macOS"):
                probe.preflight("llama3.2:1b", "a"*64)
        with patch.object(probe.platform, "system", return_value="Darwin"):
            with patch.object(probe.subprocess, "run") as pm:
                pm.return_value.stdout = "Now drawing from 'Battery Power'"
                with self.assertRaisesRegex(ValueError, "AC power"):
                    probe.preflight("llama3.2:1b", "a"*64)
                pm.return_value.stdout = "Now drawing from 'AC Power'"
                with patch.object(probe, "_probe_fixed_json", side_effect=[
                    {"models": [{"name": "llama3.2:1b", "digest": "b"*64}]},
                ]):
                    with self.assertRaisesRegex(ValueError, "digest differs"):
                        probe.preflight("llama3.2:1b", "a"*64)

    def test_real_execution_is_explicit_and_never_reports_causal_speedup(self):
        def fake_run(endpoint, model, dataset, **kw):
            self.assertIn(endpoint, probe.ENDPOINTS.values())
            self.assertTrue(kw["stream"])
            self.assertEqual(kw["warmup"], 1)
            return {
                "successes": 12, "failures": 0,
                "errors": {}, "requests_per_second": 0.25,
                "latency_ms": {"p50": 200, "p95": 300},
                "ttft_ms": {"p50": 20, "p95": 35},
                "completion_token_coverage": 12,
                "observations": [{"index": i, "success": True} for i in range(12)],
            }
        with patch.object(probe, "preflight"), \
             patch.object(probe.runner, "read_dataset",
                          return_value=([[{"role": "user", "content": "synthetic"}]], "c"*64)), \
             patch.object(probe.runner, "run", side_effect=fake_run) as runner:
            with redirect_stdout(StringIO()):
                result = probe.run_local(
                    "llama3.2:1b", "a"*64, 12, 64, self.folder)
        self.assertEqual(runner.call_count, 12)
        self.assertEqual(result["causal_speedup_verified"], False)
        self.assertEqual(len(result["results"]), 12)
        self.assertEqual(len(list(self.folder.glob("c*-phase*.json"))), 12)
        self.assertTrue((self.folder / "summary.json").exists())
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.folder/"summary.json").stat().st_mode), 0o600)
        self.assertEqual(result["expected_model_digest"], "a"*64)
        self.assertNotIn("synthetic", (self.folder/"summary.json").read_text())
        self.assertNotIn("prompt", (self.folder/"summary.json").read_text())
        self.assertIn("not_speedup", result["classification"])

    def test_phase_failure_keeps_private_partial_evidence_and_no_summary(self):
        attempts = 0
        def fake_run(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise RuntimeError("secret untrusted error body")
            return {
                "successes": 12, "failures": 0, "errors": {},
                "requests_per_second": 0.2,
                "latency_ms": {"p50": 1, "p95": 2},
                "ttft_ms": {"p50": 1, "p95": 2},
                "completion_token_coverage": 12,
            }
        with patch.object(probe, "preflight"), \
             patch.object(probe.runner, "read_dataset",
                          return_value=([[]], "c"*64)), \
             patch.object(probe.runner, "run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "partial evidence"):
                probe.run_local("llama3.2:1b", "a"*64, 12, 64, self.folder)
        self.assertEqual(len(list(self.folder.iterdir())), 1)
        self.assertFalse((self.folder/"summary.json").exists())

    def test_local_health_probes_are_exact_allowlisted(self):
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            probe._probe_fixed_json("https://attacker.example/api/tags")


if __name__ == "__main__":
    unittest.main()
