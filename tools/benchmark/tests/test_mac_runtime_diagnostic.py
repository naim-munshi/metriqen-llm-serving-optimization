"""Synthetic-only tests for safe Mac startup diagnostics. No real processes."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tools.benchmark import mac_runtime_diagnostic as diag


class Diagnostics(unittest.TestCase):
    def probe(self, *, power="AC", cli_mismatch=False, parallel=1,
              listening=1, installed=True, resident=True, named=True,
              healthy=True, system="Darwin"):
        def command(argv):
            if argv == ["pmset", "-g", "batt"]:
                return "Now drawing from '" + power + " Power'\n"
            if argv == ["ollama", "--version"]:
                return ("ollama version is 0.40.11\n" +
                        ("Warning: client version is 0.30.8\n" if cli_mismatch else ""))
            if argv == ["lsof", "-nP", "-F", "p", "-iTCP:11434", "-sTCP:LISTEN"]:
                return "".join("p" + str(4168 + i) + "\n" for i in range(listening))
            if argv[:3] == ["ps", "-p", "4168"]:
                return "/usr/local/bin/ollama\n" if named else "/tmp/other-process\n"
            if argv == ["ps", "eww", "-p", "4168"]:
                return ("TOP_SECRET=NO_LEAK USER=PRIVATE " +
                        ("" if parallel is None else "OLLAMA_NUM_PARALLEL=" + str(parallel)))
            raise AssertionError("unexpected command: " + repr(argv))

        def api(url):
            if url == diag.SERVER + "/api/version":
                return {"version": "0.40.11"}
            if url == diag.SERVER + "/api/tags":
                return {"models": [{"name": diag.MODEL, "digest": "a" * 64}]} if installed else {"models": []}
            if url == diag.SERVER + "/api/ps":
                return {"models": [{"name": diag.MODEL}]} if resident else {"models": []}
            if url == diag.GATEWAY + "/health/live":
                return {"service": "metriqen-inference-gateway", "status": "ok"} if healthy else None
            raise AssertionError("unapproved URL: " + url)

        return diag.collect(command_fn=command, json_fn=api, system=system)

    def test_on_ac_matching_config_still_cannot_attest_slots(self):
        report = self.probe()
        self.assertEqual(report["benchmark_blockers"], [])
        self.assertFalse(report["benchmark_ready"])
        self.assertFalse(report["observed"]["runtime_slots_attested"])
        self.assertEqual(report["observed"]["power"], "AC")
        self.assertEqual(report["observed"]["ollama_parallelism_configured"], 1)

    def test_owner_observed_battery_mismatch_and_unset_parallel_stay_blocked(self):
        report = self.probe(power="Battery", cli_mismatch=True, parallel=None, resident=False)
        self.assertEqual(report["observed"]["server_version"], "0.40.11")
        self.assertEqual(report["observed"]["client_version_if_warning"], "0.30.8")
        self.assertFalse(report["observed"]["model_resident"])
        self.assertTrue({"ac_power_required", "ollama_client_server_version_mismatch",
                         "ollama_parallelism_not_explicitly_verified"}.issubset(
                             set(report["benchmark_blockers"])))
        text = json.dumps(report)
        for secret in ("TOP_SECRET", "NO_LEAK", "USER=PRIVATE", "4168",
                       "/usr/local/bin", "a" * 64):
            self.assertNotIn(secret, text)

    def test_missing_or_duplicate_listener_never_guesses_parallelism(self):
        for n in (0, 2):
            with self.subTest(n=n):
                r = self.probe(listening=n)
                self.assertIsNone(r["observed"]["ollama_parallelism_configured"])
                self.assertIn("ollama_listener_identity_unverified", r["benchmark_blockers"])
                self.assertIn("ollama_parallelism_not_explicitly_verified", r["benchmark_blockers"])

    def test_non_ollama_listener_rejects_environment(self):
        r = self.probe(named=False)
        self.assertIsNone(r["observed"]["ollama_parallelism_configured"])
        self.assertFalse(r["observed"]["ollama_listener_named_process"])

    def test_unloaded_installed_model_is_not_automatically_broken(self):
        r = self.probe(resident=False)
        self.assertTrue(r["observed"]["model_installed"])
        self.assertFalse(r["observed"]["model_resident"])
        self.assertNotIn("model_tag_not_installed_verified", r["benchmark_blockers"])

    def test_model_gateway_and_os_fail_closed(self):
        r = self.probe(installed=False, healthy=False, system="Linux")
        self.assertIn("model_tag_not_installed_verified", r["benchmark_blockers"])
        self.assertIn("gateway_not_healthy", r["benchmark_blockers"])
        self.assertIn("macos_not_confirmed", r["benchmark_blockers"])

    def test_never_accept_arbitrary_network_endpoint(self):
        for url in ("https://example.com", "http://127.0.0.1:8081/auth/token",
                    "http://localhost:11434/api/tags"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                diag.local_json(url)

    def test_cli_prints_only_diagnostic_data_and_remains_no_approval(self):
        with patch.object(diag, "collect", return_value=self.probe()):
            from io import StringIO
            from contextlib import redirect_stdout
            f = StringIO()
            with redirect_stdout(f):
                self.assertEqual(diag.main([]), 0)
            self.assertIn("NOT benchmark approval", f.getvalue())
            self.assertIn('"benchmark_ready": false', f.getvalue())


if __name__ == "__main__":
    unittest.main()
