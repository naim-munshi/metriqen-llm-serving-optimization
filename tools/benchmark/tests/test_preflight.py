"""Synthetic, no-inference macOS preflight tests. Never probe a real server."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.benchmark import preflight


DIGEST = "a" * 64


class PreflightTests(unittest.TestCase):
    def make_probe(self, *, power="AC", parallel=1, digest=DIGEST,
                   gateway=True, count=1, version="0.30.8", system="Darwin"):
        def command(args):
            if args == ["pmset", "-g", "batt"]:
                return f"Now drawing from '{power} Power'\n"
            if args == ["pgrep", "-fl", "ollama serve"]:
                return "".join(f"{100 + i} ollama serve\n" for i in range(count))
            if args[:3] == ["ps", "eww", "-p"]:
                return ("USER SECRET_ACCESS_KEY=NEVER_EXPORT "
                        + (f"OLLAMA_NUM_PARALLEL={parallel}" if parallel is not None else ""))
            if args == ["memory_pressure"]:
                return "System-wide memory free percentage: 30%\n"
            raise AssertionError(f"unexpected command: {args}")

        def api(url):
            if url == preflight.MODEL_API + "/api/version":
                return {"version": version} if version is not None else None
            if url == preflight.MODEL_API + "/api/tags":
                return {"models": [{"name": "llama3.2:1b", "digest": digest}]}
            if url == preflight.GATEWAY_API + "/health/live":
                return {"service": "metriqen-inference-gateway", "status": "ok"} if gateway else None
            raise AssertionError(f"unexpected HTTP request: {url}")

        return preflight.collect(expected_model_digest=DIGEST,
                                 expected_parallelism=1, command_fn=command,
                                 json_fn=api, system=system,
                                 timestamp_utc="2026-10-13T15:00:00+00:00")

    def test_ac_server_gateway_identity_pass_but_slots_remain_unverified(self):
        result = self.make_probe()
        self.assertEqual(result["status"], "preflight_checks_passed_slot_verification_required")
        self.assertEqual(result["blocking_reasons"], [])
        self.assertFalse(result["active_slots_verified"])
        self.assertEqual(result["observed"]["system_memory_free_percent"], 30)
        self.assertNotIn("SECRET_ACCESS_KEY", json.dumps(result))
        self.assertNotIn("NEVER_EXPORT", json.dumps(result))
        self.assertNotIn("USER", json.dumps(result))

    def test_battery_refuses_measurements(self):
        result = self.make_probe(power="Battery")
        self.assertEqual(result["status"], "preflight_blocked")
        self.assertTrue(any("AC power" in reason for reason in result["blocking_reasons"]))

    def test_wrong_digest_blocks(self):
        result = self.make_probe(digest="b" * 64)
        self.assertEqual(result["status"], "preflight_blocked")
        self.assertTrue(any("digest" in reason for reason in result["blocking_reasons"]))

    def test_unhealthy_gateway_blocks(self):
        self.assertEqual(self.make_probe(gateway=False)["status"], "preflight_blocked")

    def test_missing_configured_parallelism_blocks(self):
        result = self.make_probe(parallel=None)
        self.assertEqual(result["observed"]["configured_parallelism"], None)
        self.assertEqual(result["status"], "preflight_blocked")

    def test_wrong_parallelism_blocks(self):
        self.assertEqual(self.make_probe(parallel=2)["status"], "preflight_blocked")

    def test_duplicate_ollama_processes_block(self):
        self.assertEqual(self.make_probe(count=2)["status"], "preflight_blocked")

    def test_missing_ollama_process_blocks(self):
        self.assertEqual(self.make_probe(count=0)["status"], "preflight_blocked")

    def test_unsupported_os_blocks(self):
        self.assertEqual(self.make_probe(system="Linux")["status"], "preflight_blocked")

    def test_missing_version_blocks(self):
        self.assertEqual(self.make_probe(version=None)["status"], "preflight_blocked")

    def test_invalid_inputs_rejected(self):
        for digest, parallel in [("invalid", 1), (DIGEST, 3), (DIGEST, True)]:
            with self.assertRaises(ValueError):
                preflight.collect(expected_model_digest=digest, expected_parallelism=parallel)

    def test_cli_private_json_nonoverwrite_and_blocked_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preflight.json"
            with patch.object(preflight, "collect", return_value=self.make_probe()):
                argv = ["--expected-model-digest", DIGEST, "--expected-parallelism", "1",
                        "--output", str(path)]
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(preflight.main(argv), 0)
                self.assertTrue(json.loads(path.read_text())["active_slots_verified"] is False)
                original = path.read_text()
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(preflight.main(argv), 2)
                self.assertEqual(path.read_text(), original)

            blocked = self.make_probe(power="Battery")
            with patch.object(preflight, "collect", return_value=blocked):
                argv[-1] = str(Path(tmp) / "blocked.json")
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(preflight.main(argv), 2)
                self.assertEqual(json.loads(Path(argv[-1]).read_text())["status"], "preflight_blocked")

    def test_no_untrusted_endpoint(self):
        with self.assertRaisesRegex(ValueError, "unknown local"):
            preflight._get_json("https://example.com")


if __name__ == "__main__":
    unittest.main()
