"""Offline contract tests of guarded P3 operator workflow. No real inference."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.benchmark import admission_phase as tool


DIGEST = "a" * 64
GATEWAY = "http://127.0.0.1:8081/v1/chat/completions"


def passed() -> dict:
    return {
        "status": "preflight_checks_passed_slot_verification_required",
        "observed": {"ollama_version": "0.40.6", "power_source": "AC",
                     "configured_parallelism": 2},
        "expected": {"model_digest": DIGEST, "configured_parallelism": 2},
    }


class GuardedAdmissionPhaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.private = Path(temp.name) / "reports" / "local"
        self.private.mkdir(parents=True)
        self.patch = patch.object(tool.phase_run, "PRIVATE", self.private)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def run_phase(self, name: str = "A"):
        return self.private / name

    @staticmethod
    def fake_sweep(argv):
        destination = Path(argv[argv.index("--output-dir") + 1])
        destination.mkdir(parents=True)
        (destination / "admission-summary.json").write_text(
            json.dumps({"kind": "synthetic_test_placeholder"}), encoding="utf-8"
        )
        return 0

    @staticmethod
    def fake_quality(argv):
        target = Path(argv[argv.index("--output") + 1])
        target.write_text('{"kind": "synthetic_test_placeholder"}', encoding="utf-8")
        return 0

    def collect(self, name="A"):
        return tool.collect(phase=name, digest=DIGEST, parallelism=2,
                            hardware="Apple M2 Metal", directory=self.run_phase(name))

    def test_both_phases_keep_same_ollama_setting_only_queue_depth_changes(self):
        with (patch.object(tool.preflight, "collect", side_effect=lambda **_: passed()) as pre,
              patch.object(tool.admission_load, "admission_snapshot") as snap,
              patch.object(tool.admission_load, "main", side_effect=self.fake_sweep) as load,
              patch.object(tool.online, "main", side_effect=self.fake_quality) as quality):
            self.collect("A")
            self.collect("B")
        self.assertEqual(pre.call_count, 4)
        self.assertEqual(snap.call_count, 2)
        self.assertEqual(load.call_count, 2)
        self.assertEqual(quality.call_count, 2)
        self.assertEqual([x.args[1] for x in snap.call_args_list], [0, 2])
        for phase, depth in (("A", "0"), ("B", "2")):
            args = next(call.args[0] for call in load.call_args_list
                        if call.args[0][call.args[0].index("--expected-queue-depth") + 1] == depth)
            self.assertEqual(args[args.index("--runtime") + 1], "ollama-0.40.6")
            self.assertEqual(args[args.index("--max-tokens") + 1], "128")
            self.assertEqual(args[args.index("--repeats") + 1], "3")
            self.assertEqual(args[args.index("--requests") + 1], "36")
            directory = self.run_phase(phase)
            for filename in ("preflight.json", "postflight.json", "quality.json",
                             "overload/admission-summary.json"):
                file = directory / filename
                self.assertTrue(file.is_file(), file)
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertEqual((directory / "overload").stat().st_mode & 0o777, 0o700)

    def test_battery_blocks_before_any_inference_and_retains_private_snapshot(self):
        snapshot = passed()
        snapshot["status"] = "preflight_blocked"
        snapshot["observed"]["power_source"] = "Battery"
        with (patch.object(tool.preflight, "collect", return_value=snapshot),
              patch.object(tool.admission_load, "admission_snapshot") as snap,
              patch.object(tool.admission_load, "main") as load,
              patch.object(tool.online, "main") as quality):
            with self.assertRaisesRegex(tool.AdmissionPhaseError, "preflight_blocked"):
                self.collect()
            snap.assert_not_called()
            load.assert_not_called()
            quality.assert_not_called()
        self.assertEqual((self.run_phase() / "preflight.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.run_phase() / "overload").exists())

    def test_wrong_queue_depth_blocks_before_any_inference(self):
        with (patch.object(tool.preflight, "collect", return_value=passed()),
              patch.object(tool.admission_load, "admission_snapshot",
                           side_effect=ValueError("gateway queue depth differs")),
              patch.object(tool.admission_load, "main") as load):
            with self.assertRaisesRegex(ValueError, "gateway queue"):
                self.collect()
            load.assert_not_called()
        self.assertFalse((self.run_phase() / "overload").exists())

    def test_output_path_must_be_fresh_and_private(self):
        with patch.object(tool.preflight, "collect") as pre:
            with self.assertRaisesRegex(tool.AdmissionPhaseError, "new_private"):
                tool.collect(phase="A", digest=DIGEST, parallelism=2,
                             hardware="Apple M2 Metal",
                             directory=self.private.parent / "notprivate")
            self.run_phase().mkdir()
            with self.assertRaisesRegex(tool.AdmissionPhaseError, "new_private"):
                self.collect()
            pre.assert_not_called()

    def test_missing_quality_output_cannot_pass(self):
        with (patch.object(tool.preflight, "collect", return_value=passed()),
              patch.object(tool.admission_load, "admission_snapshot"),
              patch.object(tool.admission_load, "main", side_effect=self.fake_sweep),
              patch.object(tool.online, "main", return_value=0)):
            with self.assertRaisesRegex(tool.AdmissionPhaseError, "quality_failed"):
                self.collect()
        self.assertTrue((self.run_phase() / "overload/admission-summary.json").exists())
        self.assertFalse((self.run_phase() / "postflight.json").exists())

    def test_power_switch_at_end_blocks_full_phase(self):
        bad = passed()
        bad["status"] = "preflight_blocked"
        bad["observed"]["power_source"] = "Battery"
        with (patch.object(tool.preflight, "collect", side_effect=[passed(), bad]),
              patch.object(tool.admission_load, "admission_snapshot"),
              patch.object(tool.admission_load, "main", side_effect=self.fake_sweep),
              patch.object(tool.online, "main", side_effect=self.fake_quality)):
            with self.assertRaisesRegex(tool.AdmissionPhaseError, "postflight_blocked"):
                self.collect()
        end = json.loads((self.run_phase() / "postflight.json").read_text())
        self.assertEqual(end["observed"]["power_source"], "Battery")

    def test_cli_bad_directory_does_not_collect(self):
        with patch.object(tool.preflight, "collect") as pre:
            self.assertEqual(tool.main([
                "--phase", "A", "--expected-model-digest", DIGEST,
                "--expected-parallelism", "2", "--hardware", "Apple M2 Metal",
                "--output-dir", str(self.private.parent / "outside"),
            ]), 2)
            pre.assert_not_called()


if __name__ == "__main__":
    unittest.main()
