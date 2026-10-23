"""Synthetic no-network tests for operator-only SSE 1 KiB/4 KiB A/B/A/B."""
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.benchmark import sse_abab as exp


class SSEABABContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name) / "reports" / "local"
        self.private.mkdir(parents=True, mode=0o700)
        self.private.chmod(0o700)
        target_patch = patch.object(exp, "PRIVATE", self.private)
        target_patch.start()
        self.addCleanup(target_patch.stop)
        self.digest = "a" * 64

    @staticmethod
    def fake_measure(*args, **kwargs):
        count = kwargs["requests"]
        return {
            "successes": count, "failures": 0, "requests_per_second": 0.24,
            "latency_ms": {"p95": 8000},
            "ttft_ms": {"p95": 500},
            "completion_token_coverage": count,
            "observations": [{"index": i, "answer": "PRIVATE_NOT_PERSISTED"}
                             for i in range(count)],
        }

    def collect(self, phase, **changes):
        buffer_size = exp.PHASE_BUFFER[phase]
        fake_gateway = lambda: {
            "pid": 91234, "buffer_bytes": buffer_size,
            "evidence": "process_env_4096" if buffer_size == 4096 else "env_unset_assumed_default",
            "binary_source_sha_verified": False,
        }
        fn = changes.pop("inspect", fake_gateway)
        with patch.object(exp.platform, "system", return_value="Darwin"):
            return exp.collect(
                phase, "llama3.2:1b", self.digest,
                self.private / f"{phase}.json",
                inspect=fn, runtime=lambda *_: None,
                measure=changes.pop("measure", self.fake_measure),
                requests=changes.pop("requests", 12), **changes
            )

    def test_plan_has_no_activity_and_collection_needs_explicit_opt_in(self):
        with patch.object(exp, "collect", side_effect=AssertionError("should not collect")):
            with redirect_stdout(StringIO()) as out:
                self.assertEqual(exp.main(["plan"]), 0)
                self.assertEqual(exp.main([
                    "collect", "--phase", "A1", "--expected-digest", self.digest,
                    "--output", str(self.private / "A1.json"),
                ]), 2)
        self.assertIn("PLAN ONLY", out.getvalue())
        self.assertEqual(list(self.private.iterdir()), [])

    def test_only_four_declared_phases_and_fixed_loopback_inference(self):
        self.assertEqual(exp.PHASE_BUFFER, {"A1": 1024, "B1": 4096, "A2": 1024, "B2": 4096})
        self.assertEqual(exp.LEVELS, (1, 2, 4))
        self.assertEqual(exp.ENDPOINT, "http://127.0.0.1:8081/v1/chat/completions")
        with self.assertRaisesRegex(exp.PhaseRefused, "invalid_phase"):
            exp.collect(
                "not-a-phase", "llama3.2:1b", self.digest,
                self.private / "illegal.json",
                inspect=lambda: {},
                runtime=lambda *_: None,
                measure=self.fake_measure,
            )

    def test_explicit_running_configuration_must_match_phase(self):
        actual = {
            "pid": 20, "buffer_bytes": 1024,
            "evidence": "env_unset_assumed_default",
            "binary_source_sha_verified": False,
        }
        with self.assertRaisesRegex(exp.PhaseRefused, "buffer_does_not_match_phase"):
            self.collect("B1", inspect=lambda: actual)
        self.assertEqual(list(self.private.iterdir()), [])

    def test_bad_power_digest_or_wrong_os_rejected_before_request(self):
        with patch.object(exp.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(exp.PhaseRefused, "macos_required"):
                exp.verify_environment("llama3.2:1b", self.digest, "A1")
        with patch.object(exp.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(exp.PhaseRefused, "invalid_expected_digest"):
                exp.verify_environment("llama3.2:1b", "fake", "A1")
            with self.assertRaisesRegex(exp.PhaseRefused, "preflight_failed"):
                exp.verify_environment("llama3.2:1b", self.digest, "A1",
                    runtime_check=lambda *_: (_ for _ in ()).throw(ValueError("not AC")),
                    gateway_check=lambda: {})
        self.assertEqual(list(self.private.iterdir()), [])

    def test_process_check_never_logs_untrusted_environment(self):
        def ps_output(args):
            if args[0] == "lsof":
                return "71234\n"
            return "COMMAND secrets=PRIVATE_TOKEN METRIQEN_SSE_RELAY_BUFFER_BYTES=4096\n"
        with patch.object(exp, "_capture", side_effect=ps_output):
            snap = exp.inspect_gateway()
        self.assertEqual(snap["buffer_bytes"], 4096)
        self.assertEqual(snap["pid"], 71234)
        self.assertNotIn("PRIVATE_TOKEN", str(snap))
        with patch.object(exp, "_capture", side_effect=lambda a:
                          "22\n" if a[0] == "lsof" else
                          "METRIQEN_SSE_RELAY_BUFFER_BYTES=9999"):
            with self.assertRaisesRegex(exp.PhaseRefused, "unsupported_running"):
                exp.inspect_gateway()

    def test_three_real_runner_calls_are_sanitized_and_not_speedup(self):
        calls = []
        def measure(*args, **kwargs):
            calls.append((args[0], kwargs["concurrency"], kwargs["stream"],
                          kwargs["warmup"], kwargs["max_tokens"]))
            return self.fake_measure(*args, **kwargs)
        report = self.collect("A1", measure=measure)
        self.assertEqual([v[1] for v in calls], [1, 2, 4])
        self.assertTrue(all(v[0] == exp.ENDPOINT and v[2] is True and
                            v[3] == 1 and v[4] == 64 for v in calls))
        self.assertEqual(len(report["results"]), 3)
        self.assertEqual(report["model_digest"], self.digest)
        self.assertFalse(report["causal_optimization_verified"])
        self.assertFalse(report["p3_gate_approved"])
        raw = (self.private / "A1.json").read_text()
        self.assertNotIn("PRIVATE_NOT_PERSISTED", raw)
        self.assertNotIn("observations", raw)
        self.assertIn("source_sha_verified", raw)
        self.assertFalse(report["matched_answer_quality_verified"])

    def test_no_report_when_gateway_switches_midphase(self):
        call = 0
        def snapshot():
            nonlocal call
            call += 1
            return {
                "pid": 22 + int(call > 2), "buffer_bytes": 1024,
                "evidence": "env_unset_assumed_default",
                "binary_source_sha_verified": False,
            }
        with self.assertRaisesRegex(exp.PhaseRefused, "process_changed"):
            self.collect("A1", inspect=snapshot)
        self.assertFalse((self.private/"A1.json").exists())

    def test_failed_measurement_does_not_write_success_report(self):
        with self.assertRaises(RuntimeError):
            self.collect("A1", measure=lambda *_a, **_kw: (_ for _ in ())
                         .throw(RuntimeError("synthetic interrupted")))
        self.assertFalse((self.private / "A1.json").exists())

    def test_untrusted_paths_and_existing_files_denied(self):
        for path in (
            self.private / ".." / "outside.json",
            self.private / "nested" / "file.json",
            Path(self.temp.name) / "other.json",
            self.private / "missing_extension",
        ):
            with self.subTest(path=path), self.assertRaises(exp.PhaseRefused):
                exp.output_location(path)
        existing = self.private / "A1.json"
        existing.write_text("private evidence", encoding="utf-8")
        with self.assertRaises(exp.PhaseRefused):
            exp.output_location(existing)
        existing.unlink()
        self.private.chmod(0o755)
        with self.assertRaises(exp.PhaseRefused):
            exp.output_location(existing)

    def test_four_phase_review_never_approves_or_hides_failures(self):
        paths = {}
        for phase in exp.PHASE_BUFFER:
            self.collect(phase)
            paths[phase] = self.private / f"{phase}.json"
        output = exp.review(paths)
        self.assertFalse(output["causal_speedup_verified"])
        self.assertFalse(output["p3_gate_approved"])
        self.assertTrue(output["within_setting_drift_requires_human_review"])
        self.assertEqual(output["by_concurrency"]["2"]["failures_by_phase"],
                         dict.fromkeys(exp.PHASE_BUFFER, 0))
        doc = json.loads(paths["B2"].read_text())
        doc["causal_optimization_verified"] = True
        paths["B2"].write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(exp.PhaseRefused):
            exp.review(paths)


if __name__ == "__main__":
    unittest.main()
