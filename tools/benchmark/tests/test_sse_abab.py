"""Synthetic no-network tests for operator-only SSE 1 KiB/4 KiB A/B/A/B."""
from contextlib import redirect_stdout
from io import StringIO
import hashlib
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
        self.binary = self.private / "sse-abab-gateway"
        self.binary.write_bytes(b"synthetic-unit-test-executable-not-real-gateway")
        self.binary.chmod(0o700)
        self.bin_sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        binary_patch = patch.object(exp, "EXPERIMENT_BINARY", self.binary)
        binary_patch.start()
        self.addCleanup(binary_patch.stop)
        target_patch = patch.object(exp, "PRIVATE", self.private)
        target_patch.start()
        self.addCleanup(target_patch.stop)
        self.native_executable_probe = exp._macos_running_executable
        # CI runs on Linux; live Darwin PID inspection is exercised separately.
        # All synthetic gateway probes use an explicitly expected main binary.
        process_patch = patch.object(
            exp, "_macos_running_executable",
            side_effect=lambda _pid: self.binary.resolve(),
        )
        process_patch.start()
        self.addCleanup(process_patch.stop)
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
            "binary_path_verified": True,
                "mac_proc_pidpath_verified": True,
            "upstream_origin_verified": True,
            "upstream_origin": exp.EXPECTED_UPSTREAM,
            "binary_sha256": self.bin_sha,
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
        self.assertEqual([p.name for p in self.private.iterdir()], ["sse-abab-gateway"])

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
            "binary_path_verified": True,
                "mac_proc_pidpath_verified": True,
            "upstream_origin_verified": True,
            "upstream_origin": exp.EXPECTED_UPSTREAM,
            "binary_sha256": self.bin_sha,
            "binary_source_sha_verified": False,
        }
        with self.assertRaisesRegex(exp.PhaseRefused, "buffer_does_not_match_phase"):
            self.collect("B1", inspect=lambda: actual)
        self.assertEqual([p.name for p in self.private.iterdir()], ["sse-abab-gateway"])

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
        self.assertEqual([p.name for p in self.private.iterdir()], ["sse-abab-gateway"])

    def test_kernel_main_executable_must_match_even_if_lsof_txt_lists_expected(self):
        def mapped_only(argv):
            if "-t" in argv:
                return "71234\n"
            if "-d" in argv:
                # A helper process can map this file without executing it.
                return "p71234\nftxt\nn" + str(self.binary.resolve()) + "\n"
            raise AssertionError("Mismatched main executable must block before env inspection")

        with patch.object(exp, "_capture", side_effect=mapped_only), patch.object(
            exp, "_macos_running_executable", return_value=Path("/tmp/other-server")
        ), self.assertRaisesRegex(exp.PhaseRefused, "gateway_main_executable_mismatch"):
            exp.inspect_gateway()
        self.assertFalse((self.private / "A1.json").exists())

    def test_macos_proc_pidpath_reads_kernel_executable_and_fails_closed(self):
        # setUp installs a fake native probe for all cross-platform synthetic
        # gateway tests; test the *actual* implementation here.
        probe = self.native_executable_probe
        expected = str(self.binary.resolve()).encode()
        def fake_path(pid, target, size):
            self.assertEqual(pid, 71234)
            self.assertGreater(size, len(expected))
            exp.ctypes.memmove(target, expected + b"\0", len(expected) + 1)
            return len(expected) + 1
        with patch.object(exp.ctypes, "CDLL", return_value=type(
            "FakeLib", (), {"proc_pidpath": staticmethod(fake_path)}
        )()):
            self.assertEqual(probe(71234), self.binary.resolve())

        def unavailable(_pid, _target, _size):
            return 0
        with patch.object(exp.ctypes, "CDLL", return_value=type(
            "FakeLib", (), {"proc_pidpath": staticmethod(unavailable)}
        )()), self.assertRaisesRegex(exp.PhaseRefused, "mac_executable_identity_unavailable"):
            probe(71234)

        with patch.object(exp.ctypes, "CDLL", side_effect=OSError("not Darwin")), \
                self.assertRaisesRegex(exp.PhaseRefused, "mac_executable_identity_unavailable"):
            probe(71234)

    def test_offline_rejects_historical_phase_without_kernel_pid_attestation(self):
        paths = {}
        for phase in exp.PHASE_BUFFER:
            self.collect(phase)
            paths[phase] = self.private / f"{phase}.json"
        old = json.loads(paths["A1"].read_text())
        old["gateway_process"].pop("mac_proc_pidpath_verified")
        paths["A1"].write_text(json.dumps(old), encoding="utf-8")
        with self.assertRaisesRegex(exp.PhaseRefused, "active_gateway_binary_not_verified"):
            exp.review(paths)

    def test_process_check_never_logs_untrusted_environment(self):
        def process_output(args):
            if args[0] == "ps":
                return "COMMAND secrets=PRIVATE_TOKEN METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 METRIQEN_SSE_RELAY_BUFFER_BYTES=4096\n"
            if "-t" in args:
                return "71234\n"
            if "-d" in args:
                return "p71234\nftxt\nn" + str(self.binary.resolve()) + "\nftxt\nn/usr/lib/dyld\n"
            return "p71234\nn127.0.0.1:8081\n"
        with patch.object(exp, "_capture", side_effect=process_output):
            snap = exp.inspect_gateway()
        self.assertEqual(snap["buffer_bytes"], 4096)
        self.assertEqual(snap["pid"], 71234)
        self.assertEqual(snap["binary_sha256"], self.bin_sha)
        self.assertTrue(snap["binary_path_verified"])
        self.assertFalse(snap["binary_source_sha_verified"])
        self.assertNotIn("PRIVATE_TOKEN", str(snap))
        with patch.object(exp, "_capture", side_effect=lambda a:
                          "22\n" if "-t" in a else
                          "p22\nftxt\nn/tmp/go-build/old-server\n" if "-d" in a else ""):
            with self.assertRaisesRegex(exp.PhaseRefused, "not_running_expected_binary"):
                exp.inspect_gateway()

    def test_gateway_upstream_origin_is_explicit_and_fail_closed(self):
        def capture(origin):
            def fake(argv):
                if "-t" in argv:
                    return "71234\n"
                if "-d" in argv:
                    return "p71234\nftxt\nn" + str(self.binary.resolve()) + "\n"
                if argv[0] == "ps":
                    return "COMMAND PRIVATE_TOKEN=NEVER_PRINT " + origin + "\n"
                return "p71234\nn127.0.0.1:8081\n"
            return fake

        good = "METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434"
        with patch.object(exp, "_capture", side_effect=capture(good)):
            attestation = exp.inspect_gateway()
        self.assertTrue(attestation["upstream_origin_verified"])
        self.assertEqual(attestation["upstream_origin"], exp.EXPECTED_UPSTREAM)
        self.assertNotIn("NEVER_PRINT", str(attestation))

        for rejected in (
            "",
            "METRIQEN_UPSTREAM_URL=http://127.0.0.1:11435",
            "METRIQEN_UPSTREAM_URL=http://localhost:11434",
            "METRIQEN_UPSTREAM_URL=http://192.0.2.1:11434",
            good + " " + good,
        ):
            with self.subTest(origin=rejected), patch.object(
                exp, "_capture", side_effect=capture(rejected)
            ):
                with self.assertRaisesRegex(
                    exp.PhaseRefused, "gateway_upstream_origin_not_verified"
                ):
                    exp.inspect_gateway()
        self.assertFalse((self.private / "A1.json").exists())

    def test_changed_upstream_origin_refused_before_measured_request(self):
        calls = []
        def wrong_upstream():
            raise exp.PhaseRefused("gateway_upstream_origin_not_verified")

        with self.assertRaisesRegex(exp.PhaseRefused, "preflight_failed"):
            self.collect("A1", inspect=wrong_upstream,
                         measure=lambda *_a, **_kw: calls.append(True))
        self.assertEqual(calls, [])
        self.assertFalse((self.private / "A1.json").exists())

    def test_upstream_snapshot_change_midphase_refuses_private_report(self):
        seen = []
        def inspect():
            seen.append(True)
            return {
                "pid": 71234,
                "buffer_bytes": 1024,
                "evidence": "env_unset_assumed_default",
                "binary_path_verified": True,
                "mac_proc_pidpath_verified": True,
                "binary_sha256": self.bin_sha,
                "binary_source_sha_verified": False,
                "upstream_origin_verified": True,
                "upstream_origin": (
                    exp.EXPECTED_UPSTREAM if len(seen) < 3
                    else "http://127.0.0.1:11435"
                ),
            }
        attempted = []
        def measure(*args, **kwargs):
            attempted.append(kwargs["concurrency"])
            return self.fake_measure(*args, **kwargs)
        with self.assertRaisesRegex(exp.PhaseRefused, "process_changed_during_phase"):
            self.collect("A1", inspect=inspect, measure=measure)
        self.assertEqual(attempted, [1])
        self.assertFalse((self.private / "A1.json").exists())

    def test_historical_unattested_upstream_report_is_not_comparable(self):
        paths = {}
        for phase in exp.PHASE_BUFFER:
            self.collect(phase)
            paths[phase] = self.private / f"{phase}.json"
        self.assertFalse(exp.review(paths)["p3_gate_approved"])
        report = json.loads(paths["A1"].read_text())
        report["gateway_process"].pop("upstream_origin_verified")
        paths["A1"].write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(exp.PhaseRefused, "active_gateway_binary_not_verified"):
            exp.review(paths)
        report["gateway_process"]["upstream_origin_verified"] = True
        report["gateway_process"]["upstream_origin"] = "http://127.0.0.1:11435"
        paths["A1"].write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(exp.PhaseRefused, "active_gateway_binary_not_verified"):
            exp.review(paths)

    def test_old_go_run_gateway_is_rejected_before_any_inference(self):
        def old_running_process(args):
            if "-t" in args:
                return "75718\n"
            if "-d" in args:
                return "p75718\nftxt\nn/Users/test/Library/Caches/go-build/old/server\n"
            raise AssertionError("Old go-run executable must be rejected before ps")
        with patch.object(exp, "_capture", side_effect=old_running_process):
            with patch.object(exp.platform, "system", return_value="Darwin"):
                with self.assertRaisesRegex(exp.PhaseRefused, "preflight_failed"):
                    exp.verify_environment("llama3.2:1b", self.digest, "A1",
                        runtime_check=lambda *_: None,
                        gateway_check=exp.inspect_gateway)
        self.assertFalse((self.private / "A1.json").exists())

    def test_binary_missing_symlink_and_nonexecutable_fail_closed(self):
        expected, sha = exp._verified_experiment_binary()
        self.assertEqual(expected, self.binary.resolve())
        self.assertEqual(sha, self.bin_sha)
        self.binary.unlink()
        with self.assertRaisesRegex(exp.PhaseRefused, "missing_or_untrusted"):
            exp._verified_experiment_binary()
        alternate = self.private / "alternate"
        alternate.write_bytes(b"synthetic")
        self.binary.symlink_to(alternate)
        with self.assertRaisesRegex(exp.PhaseRefused, "missing_or_untrusted"):
            exp._verified_experiment_binary()
        self.binary.unlink()
        self.binary.write_bytes(b"synthetic-no-execute")
        self.binary.chmod(0o600)
        with self.assertRaisesRegex(exp.PhaseRefused, "not_executable_or_bounded"):
            exp._verified_experiment_binary()

    def test_wrong_listener_and_unsupported_buffer_are_refused(self):
        def capture(listener="n127.0.0.1:8081", setting=""):
            def fake(argv):
                if "-t" in argv:
                    return "71234\n"
                if "-d" in argv:
                    return "p71234\nftxt\nn" + str(self.binary.resolve()) + "\n"
                if argv[0] == "ps":
                    return "COMMAND gateway METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 " + setting + "\n"
                return "p71234\n" + listener + "\n"
            return fake

        for listener in (
            "n0.0.0.0:8081", "n[::1]:8081", "n127.0.0.1:9999",
            "n127.0.0.1:8081\nn0.0.0.0:8081",
            "n127.0.0.1:8081\nn[::]:8081",
            "n127.0.0.1:8081\nn192.0.2.10:8081",
            "n127.0.0.1:8081\nn127.0.0.1:8081",
            "n127.0.0.1:8081 (LISTEN)",
            "",
        ):
            with self.subTest(listener=listener), patch.object(
                exp, "_capture", side_effect=capture(listener=listener)
            ), self.assertRaisesRegex(exp.PhaseRefused, "not_loopback"):
                exp.inspect_gateway()
        # A normal single loopback listener remains fully acceptable.
        with patch.object(exp, "_capture", side_effect=capture()):
            self.assertEqual(exp.inspect_gateway()["pid"], 71234)
        for setting in (
            "METRIQEN_SSE_RELAY_BUFFER_BYTES=1024",
            "METRIQEN_SSE_RELAY_BUFFER_BYTES=8192",
            "METRIQEN_SSE_RELAY_BUFFER_BYTES=not-a-number",
        ):
            with self.subTest(setting=setting), patch.object(
                exp, "_capture", side_effect=capture(setting=setting)
            ), self.assertRaisesRegex(exp.PhaseRefused, "unsupported_running_sse_setting"):
                exp.inspect_gateway()
        with patch.object(exp, "_capture", return_value="71234\n81234\n"):
            with self.assertRaisesRegex(exp.PhaseRefused, "exactly_one_gateway_listener"):
                exp.inspect_gateway()

    def test_stale_go_run_cannot_run_measured_requests(self):
        attempted = []
        def stale_gateway(argv):
            if "-t" in argv:
                return "75718\n"
            if "-d" in argv:
                return "p75718\nftxt\nn/Users/test/Library/Caches/go-build/server\n"
            raise AssertionError("must stop before probing other process fields")
        with patch.object(exp, "_capture", side_effect=stale_gateway):
            with self.assertRaisesRegex(exp.PhaseRefused, "preflight_failed"):
                self.collect("A1", inspect=exp.inspect_gateway,
                             measure=lambda *_a, **_kw: attempted.append(True))
        self.assertFalse(attempted)
        self.assertFalse((self.private / "A1.json").exists())

    def test_live_binary_fingerprint_mutation_aborts_without_report(self):
        def capture(argv):
            if "-t" in argv:
                return "71234\n"
            if "-d" in argv:
                return "p71234\nftxt\nn" + str(self.binary.resolve()) + "\n"
            if argv[0] == "ps":
                return "COMMAND gateway METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434\n"
            return "p71234\nn127.0.0.1:8081\n"

        calls = []
        def change_binary_after_first_level(*args, **kwargs):
            calls.append(kwargs["concurrency"])
            self.binary.write_bytes(b"synthetic-binary-changed-during-phase")
            return self.fake_measure(*args, **kwargs)

        with patch.object(exp, "_capture", side_effect=capture):
            with self.assertRaisesRegex(exp.PhaseRefused, "process_changed_during_phase"):
                self.collect("A1", inspect=exp.inspect_gateway,
                             measure=change_binary_after_first_level)
        self.assertEqual(calls, [1])
        self.assertFalse((self.private / "A1.json").exists())

    def test_offline_review_rejects_inconsistent_gateway_snapshot(self):
        paths = {}
        for phase in exp.PHASE_BUFFER:
            self.collect(phase)
            paths[phase] = self.private / f"{phase}.json"
        self.assertFalse(exp.review(paths)["p3_gate_approved"])
        b1 = json.loads(paths["B1"].read_text())
        b1["gateway_process"]["buffer_bytes"] = 1024
        paths["B1"].write_text(json.dumps(b1), encoding="utf-8")
        with self.assertRaisesRegex(exp.PhaseRefused, "active_gateway_binary_not_verified"):
            exp.review(paths)
        b1["gateway_process"]["buffer_bytes"] = 4096
        b1["gateway_process"]["evidence"] = "env_unset_assumed_default"
        paths["B1"].write_text(json.dumps(b1), encoding="utf-8")
        with self.assertRaisesRegex(exp.PhaseRefused, "active_gateway_binary_not_verified"):
            exp.review(paths)
        b1["gateway_process"]["evidence"] = "process_env_4096"
        b1["gateway_process"]["pid"] = 1
        paths["B1"].write_text(json.dumps(b1), encoding="utf-8")
        with self.assertRaisesRegex(exp.PhaseRefused, "active_gateway_binary_not_verified"):
            exp.review(paths)

    def test_offline_review_rejects_missing_files_and_phase_relabeling(self):
        paths = {}
        for phase in exp.PHASE_BUFFER:
            self.collect(phase)
            paths[phase] = self.private / f"{phase}.json"
        absent = dict(paths)
        absent["A1"] = self.private / "does-not-exist.json"
        with self.assertRaisesRegex(exp.PhaseRefused, "phase_missing"):
            exp.review(absent)
        doc = json.loads(paths["A2"].read_text())
        doc["phase"] = "A1"
        paths["A2"].write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesRegex(exp.PhaseRefused, "phase_contract_invalid"):
            exp.review(paths)

    def test_old_A1_without_binary_attestation_and_changed_binary_hash_cannot_be_reviewed(self):
        paths = {}
        for phase in exp.PHASE_BUFFER:
            self.collect(phase)
            paths[phase] = self.private / f"{phase}.json"
        old = json.loads(paths["A1"].read_text())
        old["gateway_process"].pop("binary_path_verified")
        paths["A1"].write_text(json.dumps(old), encoding="utf-8")
        with self.assertRaisesRegex(exp.PhaseRefused, "active_gateway_binary_not_verified"):
            exp.review(paths)
        old["gateway_process"]["binary_path_verified"] = True
        paths["A1"].write_text(json.dumps(old), encoding="utf-8")
        later = json.loads(paths["B2"].read_text())
        later["gateway_process"]["binary_sha256"] = "b" * 64
        paths["B2"].write_text(json.dumps(later), encoding="utf-8")
        with self.assertRaisesRegex(exp.PhaseRefused, "gateway_binary_differs_across"):
            exp.review(paths)

    def test_collection_refuses_impossible_usage_coverage_before_file_write(self):
        def invalid_measure(*args, **kwargs):
            report = self.fake_measure(*args, **kwargs)
            report["completion_token_coverage"] = kwargs["requests"] + 1
            return report

        with self.assertRaisesRegex(
            exp.PhaseRefused, "invalid_runner_completion_token_coverage"
        ):
            self.collect("A1", measure=invalid_measure)
        self.assertFalse((self.private / "A1.json").exists())

    def test_review_exposes_usage_gaps_and_numeric_within_setting_drift(self):
        paths = {}
        for phase in exp.PHASE_BUFFER:
            self.collect(phase)
            paths[phase] = self.private / f"{phase}.json"
        a2 = json.loads(paths["A2"].read_text())
        a2["results"][1]["requests_per_second"] = 0.22
        paths["A2"].write_text(json.dumps(a2), encoding="utf-8")
        b2 = json.loads(paths["B2"].read_text())
        b2["results"][1]["p95_ttft_ms_success_only"] = 550
        paths["B2"].write_text(json.dumps(b2), encoding="utf-8")
        b1 = json.loads(paths["B1"].read_text())
        b1["results"][1]["completion_token_coverage"] = 9
        paths["B1"].write_text(json.dumps(b1), encoding="utf-8")

        reviewed = exp.review(paths)
        c2 = reviewed["by_concurrency"]["2"]
        self.assertEqual(c2["completion_token_coverage_by_phase"]["B1"], 9)
        self.assertEqual(c2["missing_completion_usage_by_phase"]["B1"], 3)
        self.assertEqual(c2["successes_by_phase"]["B1"], 12)
        self.assertAlmostEqual(
            c2["within_setting_drift_pct"]["throughput_rps"]["A1_to_A2"],
            -8.3333, places=4)
        self.assertEqual(
            c2["within_setting_drift_pct"]["p95_ttft_ms_success_only"]["B1_to_B2"],
            10.0)
        self.assertTrue(reviewed["incomplete_provider_token_usage_requires_human_review"])
        self.assertFalse(reviewed["causal_speedup_verified"])
        self.assertFalse(reviewed["p3_gate_approved"])

    def test_review_rejects_invalid_coverage_and_success_metrics(self):
        paths = {}
        for phase in exp.PHASE_BUFFER:
            self.collect(phase)
            paths[phase] = self.private / f"{phase}.json"
        original = json.loads(paths["A1"].read_text())
        for invalid in (-1, 13, True, "12", None):
            with self.subTest(invalid=invalid):
                modified = json.loads(json.dumps(original))
                modified["results"][0]["completion_token_coverage"] = invalid
                paths["A1"].write_text(json.dumps(modified), encoding="utf-8")
                with self.assertRaisesRegex(exp.PhaseRefused, "coverage_invalid"):
                    exp.review(paths)
        for k, value in (
            ("requests_per_second", None),
            ("p95_latency_ms_success_only", None),
            ("p95_ttft_ms_success_only", None),
        ):
            with self.subTest(metric=k):
                modified = json.loads(json.dumps(original))
                modified["results"][0][k] = value
                paths["A1"].write_text(json.dumps(modified), encoding="utf-8")
                with self.assertRaisesRegex(exp.PhaseRefused, "metrics_inconsistent"):
                    exp.review(paths)

    def test_zero_success_report_is_descriptive_and_zero_drift_is_unknown(self):
        paths = {}
        for phase in exp.PHASE_BUFFER:
            self.collect(phase)
            paths[phase] = self.private / f"{phase}.json"
        b1 = json.loads(paths["B1"].read_text())
        b1["results"][0].update({
            "successes": 0,
            "failures": 12,
            "completion_token_coverage": 0,
            "requests_per_second": 0.0,
            "p95_latency_ms_success_only": None,
            "p95_ttft_ms_success_only": None,
        })
        paths["B1"].write_text(json.dumps(b1), encoding="utf-8")
        reviewed = exp.review(paths)
        c1 = reviewed["by_concurrency"]["1"]
        self.assertEqual(c1["failures_by_phase"]["B1"], 12)
        self.assertIsNone(c1["within_setting_drift_pct"]["throughput_rps"]["B1_to_B2"])
        self.assertIsNone(
            c1["within_setting_drift_pct"]["p95_ttft_ms_success_only"]["B1_to_B2"])
        self.assertFalse(reviewed["incomplete_provider_token_usage_requires_human_review"])
        self.assertFalse(reviewed["p3_gate_approved"])

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
                "binary_path_verified": True,
                "mac_proc_pidpath_verified": True,
                "upstream_origin_verified": True,
                "upstream_origin": exp.EXPECTED_UPSTREAM,
                "binary_sha256": self.bin_sha,
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
