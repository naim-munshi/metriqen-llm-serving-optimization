"""Synthetic-only tests for fail-closed P2 phase orchestration."""
from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.benchmark import phase_run as phase

DIGEST = "a" * 64


class PhaseRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        private = root / "reports/local"
        private.mkdir(parents=True)
        data = root / "docs/benchmarks"
        data.mkdir(parents=True)
        (data / "workload.v1.jsonl").write_text('{"synthetic":true}\n')
        q = root / "docs/nlp-eval"
        q.mkdir(parents=True)
        (q / "workload.v1.jsonl").write_text('{"synthetic":true}\n')
        for symbol, value in (("ROOT", root), ("PRIVATE", private)):
            p = patch.object(phase, symbol, value)
            p.start()
            self.addCleanup(p.stop)
        self.root, self.private = root, private

    def snapshot(self, *, status="preflight_checks_passed_slot_verification_required"):
        return {
            "schema_version": 1, "kind": "local_mac_ollama_preflight",
            "timestamp_utc": "2026-10-20T00:00:00+00:00",
            "status": status,
            "expected": {
                "model": "llama3.2:1b", "model_digest": DIGEST,
                "configured_parallelism": 1,
            },
            "observed": {"ollama_version": "0.40.5"},
            "blocking_reasons": [] if status.startswith("preflight_checks_passed") else ["not AC"],
        }

    def test_failed_preflight_never_starts_sweep_or_quality_and_keeps_private_reason(self):
        out = self.private / "A1"
        with patch.object(phase.preflight, "collect", return_value=self.snapshot(status="preflight_blocked")), \
             patch.object(phase.sweep, "main") as sweep, \
             patch.object(phase.online, "main") as quality:
            with self.assertRaisesRegex(phase.PhaseError, "preflight_blocked"):
                phase.collect(phase="A1", digest=DIGEST, hardware="Apple M2 Metal", directory=out)
        sweep.assert_not_called()
        quality.assert_not_called()
        self.assertEqual((out / "preflight.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((out / "preflight.json").read_text())["blocking_reasons"], ["not AC"])

    def test_collect_uses_fixed_full_v1_sweep_and_quality_and_version(self):
        out = self.private / "A1"
        def fake_sweep(args):
            (out / "sweep").mkdir()
            return 0
        with patch.object(phase.preflight, "collect", return_value=self.snapshot()) as preflight, \
             patch.object(phase.sweep, "main", side_effect=fake_sweep) as sweep, \
             patch.object(phase.online, "main", return_value=0) as quality:
            phase.collect(phase="A1", digest=DIGEST, hardware="Apple M2 Metal", directory=out)
        preflight.assert_called_once_with(expected_model_digest=DIGEST, expected_parallelism=1)
        argv = sweep.call_args.args[0]
        for value in ("--requests", "36", "--repeats", "3", "--warmup", "2", "--max-tokens",
                      "128", "--timeout", "120", "ollama-0.40.5", DIGEST):
            self.assertIn(value, argv)
        self.assertTrue(any(str(x).endswith("docs/benchmarks/workload.v1.jsonl") for x in argv))
        qargs = quality.call_args.args[0]
        self.assertTrue(any(str(x).endswith("docs/nlp-eval/workload.v1.jsonl") for x in qargs))
        self.assertIn("ollama-0.40.5", qargs)
        self.assertFalse((out / "sweep/evidence-manifest.json").exists())

    def test_abnormal_sweep_cannot_start_quality(self):
        out = self.private / "A1"
        with patch.object(phase.preflight, "collect", return_value=self.snapshot()), \
             patch.object(phase.sweep, "main", return_value=2), \
             patch.object(phase.online, "main") as quality:
            with self.assertRaisesRegex(phase.PhaseError, "sweep_failed"):
                phase.collect(phase="A1", digest=DIGEST, hardware="Apple M2 Metal", directory=out)
        quality.assert_not_called()

    def test_collect_is_no_overwrite_and_private_root_only(self):
        out = self.private / "A1"
        def fake_sweep(args):
            (out / "sweep").mkdir()
            return 0
        with patch.object(phase.preflight, "collect", return_value=self.snapshot()), \
             patch.object(phase.sweep, "main", side_effect=fake_sweep), \
             patch.object(phase.online, "main", return_value=0):
            phase.collect(phase="A1", digest=DIGEST, hardware="Apple M2 Metal", directory=out)
            with self.assertRaisesRegex(phase.PhaseError, "no_overwrite"):
                phase.collect(phase="A1", digest=DIGEST, hardware="Apple M2 Metal", directory=out)
            with self.assertRaisesRegex(phase.PhaseError, "under_reports_local"):
                phase.collect(phase="A1", digest=DIGEST, hardware="Apple M2 Metal",
                              directory=self.root / "unsafe")

    def test_unchecked_finalize_does_not_attest_observation(self):
        out = self.private / "A1"
        out.mkdir()
        with self.assertRaisesRegex(phase.PhaseError, "explicit_real_model_observation_required"):
            phase.finalize(phase="A1", directory=out, quantization="Q8_0",
                           host_os="macOS 15", notes="Operator checked slots", confirm_real=False)

    def test_finalize_only_when_phase_model_runtime_and_observation_match(self):
        out = self.private / "A1"
        sw = out / "sweep"
        sw.mkdir(parents=True)
        (out / "preflight.json").write_text(json.dumps(self.snapshot()))
        (sw / "sweep-summary.json").write_text(json.dumps({
            "schema_version": 1,
            "environment": {
                "model": "llama3.2:1b", "model_revision": DIGEST,
                "runtime_self_reported": "ollama-0.40.5", "hardware_self_reported": "Apple M2 Metal",
            },
        }))
        manifest = sw / "evidence-manifest.json"
        def fake_bundle(args):
            manifest.write_text('{"schema_version":1}')
            return 0
        with patch.object(phase.evidence_bundle, "main", side_effect=fake_bundle) as bundle, \
             patch.object(phase.evidence_gate, "main", return_value=0) as gate:
            phase.finalize(phase="A1", directory=out, quantization="Q8_0",
                           host_os="macOS 15", notes="AC and slots manually inspected",
                           confirm_real=True)
        self.assertIn("--confirm-real-model-executed", bundle.call_args.args[0])
        self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
        gate.assert_called_once()
        self.assertFalse((out / "four-phase-pass.json").exists())

    def test_finalize_wrong_phase_or_model_does_not_create_manifest(self):
        out = self.private / "A1"
        sw = out / "sweep"
        sw.mkdir(parents=True)
        (out / "preflight.json").write_text(json.dumps(self.snapshot()))
        (sw / "sweep-summary.json").write_text(json.dumps({
            "schema_version": 1,
            "environment": {
                "model": "wrong:tag", "model_revision": DIGEST,
                "runtime_self_reported": "ollama-0.40.5",
            },
        }))
        with patch.object(phase.evidence_bundle, "main") as bundle:
            with self.assertRaisesRegex(phase.PhaseError, "do_not_match"):
                phase.finalize(phase="A1", directory=out, quantization="Q8_0",
                               host_os="macOS 15", notes="AC and slots manually inspected",
                               confirm_real=True)
        bundle.assert_not_called()

    def test_b2_requires_parallelism_two(self):
        with patch.object(phase.preflight, "collect",
                          return_value=self.snapshot(status="preflight_blocked")) as p:
            with self.assertRaisesRegex(phase.PhaseError, "preflight_blocked"):
                phase.collect(phase="B2", digest=DIGEST, hardware="Apple M2 Metal",
                              directory=self.private / "B2")
        p.assert_called_once_with(expected_model_digest=DIGEST, expected_parallelism=2)

    def test_invalid_cli_args_do_not_create_reports(self):
        with redirect_stderr(StringIO()):
            self.assertEqual(2, phase.main([
                "collect", "--phase", "A1", "--expected-model-digest", "bad",
                "--hardware", "Apple M2 Metal",
                "--output-dir", str(self.private / "A1"),
            ]))
        self.assertFalse((self.private / "A1").exists())


if __name__ == "__main__":
    unittest.main()
