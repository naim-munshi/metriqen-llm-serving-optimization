"""Offline contract tests: sampled AC state is private and fail-closed."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.benchmark.power_monitor import PowerMonitor


class PowerMonitorTests(unittest.TestCase):
    def test_ac_only_report_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "power-monitor.json"
            with PowerMonitor(path, interval_seconds=3600, reader=lambda: "AC") as monitor:
                self.assertEqual(monitor.initial_source, "AC")
            self.assertEqual(monitor.status, "ac_samples_only")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            report = json.loads(path.read_text())
            self.assertEqual(report["status"], "ac_samples_only")
            self.assertGreaterEqual(len(report["samples"]), 2)
            self.assertNotIn("process", json.dumps(report).lower())

    def test_battery_transition_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            states = iter(["AC", "Battery"])
            path = Path(tmp) / "power-monitor.json"
            with PowerMonitor(path, interval_seconds=3600,
                              reader=lambda: next(states)) as monitor:
                self.assertEqual(monitor.initial_source, "AC")
            self.assertEqual(monitor.status, "power_condition_not_maintained")
            self.assertEqual(json.loads(path.read_text())["samples"][-1]["source"],
                             "Battery")

    def test_unknown_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "power-monitor.json"
            with PowerMonitor(path, interval_seconds=3600,
                              reader=lambda: "unexpected") as monitor:
                self.assertEqual(monitor.initial_source, "unknown")
            self.assertEqual(monitor.status, "power_condition_not_maintained")

    def test_no_overwrite_of_prior_power_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "power-monitor.json"
            path.write_text("original")
            with self.assertRaises(FileExistsError):
                with PowerMonitor(path, interval_seconds=3600, reader=lambda: "AC"):
                    pass
            self.assertEqual(path.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
