"""Synthetic end-to-end report fixtures for human-attested evidence manifests.

These tests never execute a real model or attest a GPU.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from tools.benchmark import evidence_bundle, evidence_gate, runner, sweep


class EvidenceBundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sweep_dir = self.root / "experiment"
        self.sweep_dir.mkdir()
        self.config = {
            "repeats": 3, "requests_per_run": 2, "warmup_per_run": 1,
            "concurrency": [1, 2, 4], "max_tokens": 32,
            "timeout_seconds": 120.0, "stream_usage_requested": True,
        }
        self.env = {
            "model": "llama3.2:1b", "model_revision": "a" * 64,
            "runtime_self_reported": "ollama-0.30.8",
            "hardware_self_reported": "Apple M2 synthetic",
            "dataset_sha256": "b" * 64,
            "endpoint": "http://127.0.0.1:8081/v1/chat/completions",
        }
        self.rebuild_reports()
        self.quality = {
            "schema_version": 1, "model": self.env["model"],
            "model_revision_self_reported": self.env["model_revision"],
            "runtime_self_reported": self.env["runtime_self_reported"],
            "hardware_self_reported": self.env["hardware_self_reported"],
            "dataset_sha256": "c" * 64,
            "decoding": {"temperature": 0, "max_tokens": 32, "stream": False},
            "results": {
                "quality": {"sample_count": 2,
                            "exact_match": {"correct": 1, "rate": .5},
                            "normalized_exact_match": {"correct": 2, "rate": 1.0}},
                "generation": {"successful_requests": 2, "failed_requests": 0},
            },
        }
        self.save_quality()
        self.cli = [
            "--sweep-dir", str(self.sweep_dir), "--quality-report", "quality.json",
            "--host-os", "macOS 26 verified", "--quantization", "Q4_0 verified",
            "--timestamp-utc", "2026-10-12T12:00:00Z",
            "--notes", "local private test with pinned artifact",
            "--confirm-real-model-executed",
        ]

    def rebuild_reports(self):
        entries, computed = [], []
        for repeat in (1, 2, 3):
            for concurrency in (1, 2, 4):
                name = f"repeat-{repeat:02d}-c{concurrency}.json"
                samples = [
                    runner.Sample(200, True, 100.0 + repeat, 20.0, 10, 5),
                    runner.Sample(200, True, 110.0 + concurrency, 30.0, 20, 6),
                ]
                results = runner.summarize(samples, 1.0, True)
                results["observations"] = [
                    {"index": i, **asdict(sample)} for i, sample in enumerate(samples)
                ]
                report = {
                    "schema_version": 1,
                    "environment": dict(self.env),
                    "configuration": {
                        "requests": 2, "concurrency": concurrency, "warmup": 1,
                        "stream": True, "stream_usage_requested": True,
                        "max_tokens": 32, "timeout_seconds": 120.0,
                    },
                    "results": results,
                }
                (self.sweep_dir / name).write_text(json.dumps(report))
                entries.append({"path": name, "repeat": repeat, "concurrency": concurrency})
                computed.append({"concurrency": concurrency, "results": results})
        self.summary = {
            "schema_version": 1, "environment": dict(self.env),
            "configuration": dict(self.config), "run_files": entries,
            "summary": sweep.summarize_runs(computed, argparse.Namespace(repeats=3)),
        }
        self.save_summary()

    def save_summary(self):
        (self.sweep_dir / "sweep-summary.json").write_text(json.dumps(self.summary))

    def save_quality(self):
        (self.sweep_dir / "quality.json").write_text(json.dumps(self.quality))

    def call(self, *args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return evidence_bundle.main([*self.cli, *args])

    @property
    def manifest_path(self):
        return self.sweep_dir / "evidence-manifest.json"

    def test_verified_bundle_passes_existing_strict_evidence_gate(self):
        self.assertEqual(self.call(), 0)
        self.assertTrue(self.manifest_path.is_file())
        payload = self.manifest_path.read_text()
        self.assertNotIn("PRIVATE_PROMPT", payload)
        manifest = json.loads(payload)
        self.assertEqual(manifest["model_digest"], "a" * 64)
        self.assertEqual(len(manifest["benchmark_reports"]), 9)
        self.assertTrue(manifest["real_model_executed"])  # Explicit human flag.
        self.assertEqual(evidence_gate.validate(manifest), [])
        self.assertEqual(evidence_gate.verify_report_contents(manifest, self.manifest_path), [])

    def test_human_confirmation_required_before_creating_manifest(self):
        self.cli.remove("--confirm-real-model-executed")
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_do_not_overwrite_existing_manifest(self):
        self.manifest_path.write_text("KEEP")
        self.assertEqual(self.call(), 2)
        self.assertEqual(self.manifest_path.read_text(), "KEEP")

    def test_partial_sweep_refused(self):
        self.summary["run_files"].pop()
        self.save_summary()
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_duplicate_run_refused(self):
        self.summary["run_files"][1] = dict(self.summary["run_files"][0])
        self.save_summary()
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_mismatched_aggregate_refused(self):
        self.summary["summary"]["2"]["requests_per_second"]["median"] = 999
        self.save_summary()
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_wrong_raw_report_percentile_rejected(self):
        path = self.sweep_dir / "repeat-01-c1.json"
        item = json.loads(path.read_text())
        item["results"]["latency_ms"]["p95"] = 9999
        path.write_text(json.dumps(item))
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_failed_request_is_not_excluded(self):
        path = self.sweep_dir / "repeat-01-c1.json"
        item = json.loads(path.read_text())
        item["results"]["failures"] = 1
        item["results"]["successes"] = 1
        path.write_text(json.dumps(item))
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_missing_quality_report_refused(self):
        (self.sweep_dir / "quality.json").unlink()
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_quality_wrong_model_refused(self):
        self.quality["model_revision_self_reported"] = "d" * 64
        self.save_quality()
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_quality_decoding_must_match(self):
        self.quality["decoding"]["max_tokens"] = 64
        self.save_quality()
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_quality_path_must_not_escape_experiment(self):
        (self.root / "quality.json").write_text(json.dumps(self.quality))
        self.cli[self.cli.index("quality.json")] = "../quality.json"
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_invalid_or_future_timestamp_refused(self):
        position = self.cli.index("2026-10-12T12:00:00Z")
        for invalid in ("2026-02-30T12:00:00Z", "2035-01-01T00:00:00Z", "2026-10-12"):
            self.cli[position] = invalid
            self.assertEqual(self.call(), 2)
            self.assertFalse(self.manifest_path.exists())

    def test_missing_hardware_detail_refused(self):
        self.cli[self.cli.index("Apple M2 synthetic") if "Apple M2 synthetic" in self.cli else self.cli.index("macOS 26 verified")] = "unknown hardware"
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_documentation_quantization_placeholder_refused(self):
        self.cli[self.cli.index("Q4_0 verified")] = "ENTER_ACTUAL_QUANTIZATION_FROM_OLLAMA_SHOW"
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_missing_quality_hash_refused(self):
        self.quality["dataset_sha256"] = "not-a-hash"
        self.save_quality()
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())

    def test_run_report_fingerprint_mismatch_refused(self):
        path = self.sweep_dir / "repeat-01-c1.json"
        item = json.loads(path.read_text())
        item["environment"]["dataset_sha256"] = "e" * 64
        path.write_text(json.dumps(item))
        self.assertEqual(self.call(), 2)
        self.assertFalse(self.manifest_path.exists())


if __name__ == "__main__":
    unittest.main()
