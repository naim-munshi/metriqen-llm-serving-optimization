"""All fixtures are synthetic; no Mac, Ollama, gateway or GPU is contacted."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from tools.benchmark import abab_preflight_gate as gate
from tools.benchmark.tests import test_compare

PHASES = ("A1", "B1", "A2", "B2")


class FourPhasePreflightGateTests(unittest.TestCase):
    def setUp(self):
        fixture = test_compare.ComparisonTests(
            methodName="test_valid_results_are_labeled_exploratory_not_speedups")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        directories = (
            fixture.baseline, fixture.candidate,
            fixture.root / "a2", fixture.root / "b2",
        )
        fixture.write_bundle(directories[2], wall=1.1)
        fixture.write_bundle(directories[3], wall=0.7)
        self.dirs = dict(zip(PHASES, directories))
        self.manifests = {
            phase: folder / "evidence-manifest.json"
            for phase, folder in self.dirs.items()
        }
        self.preflights = {}
        for number, phase in enumerate(PHASES, 1):
            self.add_timing(phase, number)
            report = self.expected_preflight(phase, number)
            path = self.dirs[phase] / "preflight.json"
            path.write_text(json.dumps(report))
            self.preflights[phase] = path

    def add_timing(self, phase, number):
        folder = self.dirs[phase]
        summary_path = folder / "sweep-summary.json"
        summary = json.loads(summary_path.read_text())
        for index, item in enumerate(summary["run_files"]):
            path = folder / item["path"]
            raw = json.loads(path.read_text())
            start = (datetime(2026, 10, 14, number, 0, tzinfo=timezone.utc)
                     + timedelta(seconds=index * 5))
            end = start + timedelta(seconds=2)
            timing = {
                "started_at_utc": start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "finished_at_utc": end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "elapsed_seconds": 2.0,
                "scope": "warmup_and_measured_requests",
            }
            raw["timing"] = timing
            item["timing"] = timing
            path.write_text(json.dumps(raw))
        summary_path.write_text(json.dumps(summary))

    @staticmethod
    def expected_preflight(phase, number):
        parallel = {"A1": 1, "B1": 2, "A2": 1, "B2": 2}[phase]
        started = datetime(2026, 10, 14, number, 0, tzinfo=timezone.utc)
        preflight_at = started - timedelta(seconds=30)
        return {
            "schema_version": 1,
            "kind": "local_mac_ollama_preflight",
            "timestamp_utc": preflight_at.isoformat(timespec="seconds"),
            "status": "preflight_checks_passed_slot_verification_required",
            "expected": {"model": "llama3.2:1b", "model_digest": "a"*64,
                         "configured_parallelism": parallel},
            "observed": {
                "host_system": "Darwin",
                "power_source": "AC",
                "ollama_version": "0.30.8",
                "installed_model_digest": "a"*64,
                "gateway_live": True,
                "ollama_process_count": 1,
                "configured_parallelism": parallel,
                "system_memory_free_percent": 30,
            },
            "blocking_reasons": [],
            "active_slots_verified": False,
            "limitations": ["Active slots require independent operator log inspection"],
        }

    def change(self, phase, fn):
        path = self.preflights[phase]
        obj = json.loads(path.read_text())
        fn(obj)
        path.write_text(json.dumps(obj))

    def validate(self):
        return gate.validate(self.manifests, self.preflights)

    def argv(self, *, output=None):
        argv = []
        for phase in PHASES:
            option = phase.lower()
            argv.extend([f"--{option}", str(self.manifests[phase]),
                         f"--{option}-preflight", str(self.preflights[phase])])
        if output is not None:
            argv.extend(["--output", str(output)])
        return argv

    def test_four_fresh_timed_phase_preflights_pass_as_only_consistency_evidence(self):
        result = self.validate()
        self.assertEqual(result["classification"], "operator_sourced_preflight_consistency_only")
        self.assertEqual(result["timed_phase_order"], "A1_B1_A2_B2")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(set(result["phases"]), set(PHASES))
        self.assertEqual([result["phases"][p]["expected_parallelism"] for p in PHASES],
                         [1, 2, 1, 2])
        self.assertTrue(all(result["phases"][p]["preflight_to_first_run_seconds"] == 30
                            for p in PHASES))
        self.assertTrue(all(result["phases"][p]["runtime_slots_attested"] is False
                            for p in PHASES))
        raw = json.dumps(result)
        for secret in ("PRIVATE_PROMPT", "secret-token", str(self.fixture.root),
                       "ollama serve", "PRIVATE_USER"):
            self.assertNotIn(secret, raw)

    def test_cli_exclusive_private_output_and_no_real_execution_claim(self):
        target = self.fixture.root / "private" / "preflight-binding.json"
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(gate.main(self.argv(output=target)), 0)
        raw = target.read_text()
        self.assertEqual(json.loads(raw)["schema_version"], 1)
        self.assertIn("NOT VERIFIED", stdout.getvalue())
        self.assertNotIn(str(self.fixture.root), raw)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(gate.main(self.argv(output=target)), 2)
        self.assertEqual(raw, target.read_text())

    def test_readonly_cli_without_output_creates_no_evidence(self):
        before = sorted(self.fixture.root.rglob("*.json"))
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(gate.main(self.argv()), 0)
        self.assertEqual(before, sorted(self.fixture.root.rglob("*.json")))

    def test_every_phase_binding_rejects_wrong_parallelism_and_digest(self):
        for phase in PHASES:
            for key in ("expected", "observed"):
                with self.subTest(phase=phase, key=key):
                    self.change(phase, lambda data: data[key].__setitem__(
                        "configured_parallelism", 2 if phase in ("A1", "A2") else 1))
                    with self.assertRaisesRegex(gate.PreflightEvidenceError,
                                                "parallelism|settings"):
                        self.validate()
                    self.preflights[phase].write_text(json.dumps(
                        self.expected_preflight(phase, PHASES.index(phase)+1)))
            self.change(phase, lambda data: data["expected"].__setitem__(
                "model_digest", "c"*64))
            with self.assertRaisesRegex(gate.PreflightEvidenceError, "revision"):
                self.validate()
            self.preflights[phase].write_text(json.dumps(
                self.expected_preflight(phase, PHASES.index(phase)+1)))

    def test_wrong_ollama_version_runtime_and_installed_digest(self):
        for field, value in [("ollama_version", "0.88.8"),
                             ("installed_model_digest", "b"*64),
                             ("gateway_live", False),
                             ("power_source", "Battery"),
                             ("ollama_process_count", 2),
                             ("host_system", "Linux"),
                             ("system_memory_free_percent", 101)]:
            with self.subTest(field=field):
                self.change("B1", lambda data: data["observed"].__setitem__(field, value))
                with self.assertRaises(gate.PreflightEvidenceError):
                    self.validate()
                self.preflights["B1"].write_text(json.dumps(
                    self.expected_preflight("B1", 2)))

    def test_status_blockers_slots_cannot_be_fake_verified(self):
        for field, value in [
            ("status", "preflight_blocked"),
            ("blocking_reasons", ["battery"]),
            ("active_slots_verified", True),
            ("schema_version", True),
            ("limitations", []),
        ]:
            with self.subTest(field=field):
                self.change("A1", lambda data: data.__setitem__(field, value))
                with self.assertRaises(gate.PreflightEvidenceError):
                    self.validate()
                self.preflights["A1"].write_text(json.dumps(
                    self.expected_preflight("A1", 1)))

    def test_preflight_must_be_recent_before_the_phase_and_after_previous_finish(self):
        start = datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)
        invalid = [
            start - timedelta(minutes=15, seconds=1),
            start + timedelta(seconds=1),
            datetime(2026, 10, 14, 1, 0, 30, tzinfo=timezone.utc),
        ]
        for timestamp in invalid:
            with self.subTest(timestamp=timestamp):
                self.change("B1", lambda data: data.__setitem__(
                    "timestamp_utc", timestamp.isoformat()))
                with self.assertRaises(gate.PreflightEvidenceError):
                    self.validate()
                self.preflights["B1"].write_text(json.dumps(
                    self.expected_preflight("B1", 2)))
        self.change("B1", lambda data: data.__setitem__(
            "timestamp_utc", (start-timedelta(minutes=15)).isoformat()))
        self.assertEqual(self.validate()["phases"]["B1"]["preflight_to_first_run_seconds"], 900)

    def test_invalid_timestamp_formats_rejected(self):
        for bad in ("2026-10-14T00:59:30", "2026-10-14T09:59:30+09:00",
                    "2026-02-30T00:59:30Z", "2026-10-14",
                    "2026-10-14T00:59:30Z EXTRA", 42, None):
            with self.subTest(bad=bad):
                self.change("A1", lambda data: data.__setitem__("timestamp_utc", bad))
                with self.assertRaises(gate.PreflightEvidenceError):
                    self.validate()
                self.preflights["A1"].write_text(json.dumps(
                    self.expected_preflight("A1", 1)))

    def test_legacy_phase_without_run_timing_is_ineligible(self):
        for item in (self.dirs["B2"]).glob("repeat-*.json"):
            raw = json.loads(item.read_text())
            raw.pop("timing", None)
            item.write_text(json.dumps(raw))
        summary_file = self.dirs["B2"] / "sweep-summary.json"
        summary = json.loads(summary_file.read_text())
        for entry in summary["run_files"]:
            entry.pop("timing", None)
        summary_file.write_text(json.dumps(summary))
        with self.assertRaisesRegex(gate.PreflightEvidenceError, "timed"):
            self.validate()

    def test_phases_must_remain_ordered_and_nonoverlapping(self):
        self.add_timing("B1", 4)
        with self.assertRaises(ValueError):
            self.validate()

    def test_tampered_raw_benchmark_and_quality_evidence_rejected(self):
        rawfile = self.dirs["A2"] / "repeat-01-c1.json"
        raw = json.loads(rawfile.read_text())
        raw["results"]["requests_per_second"] = 123456
        rawfile.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            self.validate()
        self.add_timing("A2", 3)  # restores timings, not the tampered result
        self.fixture.write_bundle(self.dirs["A2"], wall=1.1)
        self.add_timing("A2", 3)
        qualityfile = self.dirs["A2"] / "quality.json"
        raw = json.loads(qualityfile.read_text())
        raw["model_revision_self_reported"] = "b"*64
        qualityfile.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_manifest_or_preflight_paths_rejected(self):
        self.preflights["B2"] = self.preflights["A1"]
        with self.assertRaisesRegex(gate.PreflightEvidenceError, "distinct"):
            self.validate()
        self.preflights["B2"] = self.dirs["B2"] / "preflight.json"
        self.manifests["B2"] = self.manifests["A1"]
        with self.assertRaisesRegex(gate.PreflightEvidenceError, "distinct"):
            self.validate()

    def test_symlink_oversize_duplicate_json_keys_and_private_extra_fields_rejected(self):
        saved = self.preflights["A1"]
        symlink = self.dirs["A1"] / "aliased.json"
        symlink.symlink_to(saved)
        self.preflights["A1"] = symlink
        with self.assertRaisesRegex(gate.PreflightEvidenceError, "ordinary"):
            self.validate()
        self.preflights["A1"] = saved

        saved.write_text(" " * (gate.MAX_PREFLIGHT_BYTES + 1))
        with self.assertRaisesRegex(gate.PreflightEvidenceError, "64 KiB"):
            self.validate()
        saved.write_text('{"kind":"x","kind":"y"}')
        with self.assertRaisesRegex(gate.PreflightEvidenceError, "invalid preflight JSON"):
            self.validate()
        saved.write_text('{"kind": NaN}')
        with self.assertRaisesRegex(gate.PreflightEvidenceError, "invalid preflight JSON"):
            self.validate()
        saved.write_text(json.dumps({
            **self.expected_preflight("A1", 1), "PRIVATE_SECRET": "NEVER_EXPORT",
        }))
        with self.assertRaisesRegex(gate.PreflightEvidenceError, "structure"):
            self.validate()

    def test_bool_as_int_is_never_parallelism(self):
        self.change("B1", lambda data: data["expected"].__setitem__(
            "configured_parallelism", True))
        with self.assertRaises(gate.PreflightEvidenceError):
            self.validate()
        self.preflights["B1"].write_text(json.dumps(
            self.expected_preflight("B1", 2)))
        self.change("B1", lambda data: data["observed"].__setitem__(
            "ollama_process_count", True))
        with self.assertRaises(gate.PreflightEvidenceError):
            self.validate()

    def test_cli_errors_do_not_leak_paths_or_upstream_metadata(self):
        self.change("A1", lambda data: data["expected"].__setitem__(
            "model_digest", "PRIVATE_NEVER_LEAK"))
        err = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(err):
            self.assertEqual(gate.main(self.argv()), 2)
        text = err.getvalue()
        self.assertNotIn(str(self.fixture.root), text)
        self.assertNotIn("PRIVATE_NEVER_LEAK", text)
        self.assertIn("A1", text)


if __name__ == "__main__":
    unittest.main()
