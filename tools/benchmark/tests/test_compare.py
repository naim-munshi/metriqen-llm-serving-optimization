"""Synthetic comparison fixtures: no real model execution or speedup claims."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from io import StringIO
import argparse
import json
from pathlib import Path
import tempfile
import unittest

from tools.benchmark import compare, runner, sweep


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.baseline = self.root / "baseline"
        self.candidate = self.root / "candidate"
        self.write_bundle(self.baseline, wall=1.0)
        self.write_bundle(self.candidate, wall=0.5)

    def write_bundle(self, directory, *, wall, completion_usage=True, correct=2):
        directory.mkdir(parents=True, exist_ok=True)
        env = {
            "model": "llama3.2:1b", "model_revision": "a" * 64,
            "runtime_self_reported": "ollama-0.30.8",
            "hardware_self_reported": "Apple M2 controlled",
            "dataset_sha256": "b" * 64,
            "endpoint": "http://127.0.0.1:8081/v1/chat/completions",
        }
        config = {"repeats": 3, "requests_per_run": 2, "warmup_per_run": 1,
                  "concurrency": [1, 2, 4], "max_tokens": 32,
                  "timeout_seconds": 120.0, "stream_usage_requested": True}
        entries, raw_data, manifest_entries = [], [], []
        for repeat in range(1, 4):
            for concurrency in (1, 2, 4):
                name = f"repeat-{repeat:02d}-c{concurrency}.json"
                samples = [
                    runner.Sample(200, True, 50.0 * wall + repeat, 10.0, 10 if completion_usage else None, 5),
                    runner.Sample(200, True, 60.0 * wall + concurrency, 15.0, 20 if completion_usage else None, 6),
                ]
                result = runner.summarize(samples, wall, True)
                result["observations"] = [
                    {"index": i, **asdict(item)} for i, item in enumerate(samples)
                ]
                item = {
                    "schema_version": 1, "configuration": {
                        "requests": 2, "concurrency": concurrency, "warmup": 1,
                        "stream": True, "stream_usage_requested": True,
                        "max_tokens": 32, "timeout_seconds": 120.0,
                    }, "environment": dict(env), "results": result,
                }
                (directory / name).write_text(json.dumps(item))
                entries.append({"path": name, "repeat": repeat, "concurrency": concurrency})
                manifest_entries.append({"concurrency": concurrency, "report_path": name})
                raw_data.append({"concurrency": concurrency, "results": result})
        summary = {
            "schema_version": 1, "environment": dict(env), "configuration": config,
            "run_files": entries,
            "summary": sweep.summarize_runs(raw_data, argparse.Namespace(repeats=3)),
        }
        (directory / "sweep-summary.json").write_text(json.dumps(summary))
        quality = {
            "schema_version": 1, "model": env["model"],
            "model_revision_self_reported": env["model_revision"],
            "runtime_self_reported": env["runtime_self_reported"],
            "hardware_self_reported": env["hardware_self_reported"],
            "dataset_sha256": "c" * 64,
            "decoding": {"temperature": 0, "max_tokens": 32, "stream": False},
            "results": {
                "quality": {"sample_count": 2,
                            "exact_match": {"correct": correct, "rate": correct / 2},
                            "normalized_exact_match": {"correct": correct, "rate": correct / 2}},
                "generation": {"successful_requests": 2, "failed_requests": 0},
            },
        }
        (directory / "quality.json").write_text(json.dumps(quality))
        manifest = {
            "model_tag": env["model"], "model_digest": env["model_revision"],
            "ollama_version": env["runtime_self_reported"],
            "hardware": env["hardware_self_reported"],
            "host_os": "macOS 26 controlled", "quantization": "Q4_0",
            "timestamp_utc": "2026-10-13T01:00:00Z",
            "notes": "Local internally generated test data for regression validation",
            "real_model_executed": True,
            "benchmark_reports": manifest_entries,
            "quality_report": "quality.json",
        }
        (directory / "evidence-manifest.json").write_text(json.dumps(manifest))

    def manifest(self, path):
        return path / "evidence-manifest.json"

    def report(self, name, directory=None):
        root = directory or self.candidate
        return json.loads((root / name).read_text())

    def save(self, name, value, directory=None):
        root = directory or self.candidate
        (root / name).write_text(json.dumps(value))

    def run_compare(self, **overrides):
        return compare.compare(
            self.manifest(self.baseline), self.manifest(self.candidate),
            "Changed runtime queue admission strategy", **overrides,
        )

    def test_valid_results_are_labeled_exploratory_not_speedups(self):
        result = self.run_compare()
        self.assertEqual(result["comparison_status"], "exploratory_small_sample")
        self.assertTrue(result["quality"]["non_regression"])
        self.assertEqual(result["quality"]["samples"], 2)
        item = result["metrics_by_concurrency"]["1"]["requests_per_second"]
        self.assertEqual(item["baseline_median"], 2.0)
        self.assertEqual(item["candidate_median"], 4.0)
        self.assertEqual(item["percentage_change"], 100.0)
        self.assertEqual(
            result["metrics_by_concurrency"]["4"]["latency_run_p95_ms"]["prefer"], "lower"
        )
        self.assertNotIn("PRIVATE_PROMPT", json.dumps(result))

    def test_low_sample_threshold_can_be_recorded_but_never_proves_causality(self):
        result = self.run_compare(min_quality_samples=2, min_requests_per_run=2)
        self.assertEqual(result["comparison_status"], "descriptive_not_causal")

    def test_cli_creates_new_json_without_overwriting(self):
        output = self.root / "out" / "comparison.json"
        cmd = [
            "--baseline", str(self.manifest(self.baseline)),
            "--candidate", str(self.manifest(self.candidate)),
            "--change", "Changed runtime queue admission strategy",
            "--output", str(output),
        ]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(compare.main(cmd), 0)
        self.assertEqual(json.loads(output.read_text())["schema_version"], 1)
        saved = output.read_text()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(compare.main(cmd), 2)
        self.assertEqual(saved, output.read_text())

    def test_same_evidence_folder_rejected(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            compare.compare(self.manifest(self.baseline), self.manifest(self.baseline), "New queue behavior")

    def test_missing_candidate_manifest_rejected(self):
        self.manifest(self.candidate).unlink()
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_model_digest_mismatch_rejected(self):
        manifest = self.report("evidence-manifest.json")
        manifest["model_digest"] = "d" * 64
        self.save("evidence-manifest.json", manifest)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_hardware_mismatch_rejected(self):
        manifest = self.report("evidence-manifest.json")
        manifest["hardware"] = "Other CPU"
        self.save("evidence-manifest.json", manifest)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_benchmark_dataset_mismatch_rejected(self):
        summary = self.report("sweep-summary.json")
        summary["environment"]["dataset_sha256"] = "f" * 64
        self.save("sweep-summary.json", summary)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_gateway_endpoint_mismatch_rejected(self):
        summary = self.report("sweep-summary.json")
        summary["environment"]["endpoint"] = "http://127.0.0.1:8082/v1/chat/completions"
        self.save("sweep-summary.json", summary)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_quality_reference_dataset_mismatch_rejected(self):
        quality = self.report("quality.json")
        quality["dataset_sha256"] = "e" * 64
        self.save("quality.json", quality)
        with self.assertRaisesRegex(ValueError, "quality dataset differs"):
            self.run_compare()

    def test_quality_regression_blocks_positive_interpretation(self):
        self.write_bundle(self.candidate, wall=0.5, correct=1)
        result = self.run_compare(min_quality_samples=2, min_requests_per_run=2)
        self.assertEqual(result["comparison_status"], "quality_regression_not_eligible")
        self.assertFalse(result["quality"]["non_regression"])

    def test_missing_usage_produces_null_throughput_not_a_fabrication(self):
        self.write_bundle(self.baseline, wall=1.0, completion_usage=False)
        self.write_bundle(self.candidate, wall=0.5, completion_usage=False)
        result = self.run_compare(min_quality_samples=2, min_requests_per_run=2)
        self.assertEqual(result["comparison_status"], "descriptive_missing_token_usage")
        self.assertIsNone(
            result["metrics_by_concurrency"]["1"]["output_tokens_per_second"]["percentage_change"]
        )

    def test_tampered_raw_percentile_rejected(self):
        item = self.report("repeat-01-c1.json")
        item["results"]["latency_ms"]["p95"] = 1234
        self.save("repeat-01-c1.json", item)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_tampered_aggregate_rejected(self):
        item = self.report("sweep-summary.json")
        item["summary"]["1"]["requests_per_second"]["median"] = 999
        self.save("sweep-summary.json", item)
        with self.assertRaisesRegex(ValueError, "summary disagrees"):
            self.run_compare()

    def test_failed_request_cannot_be_filtered_out(self):
        item = self.report("repeat-01-c1.json")
        item["results"]["failures"] = 1
        item["results"]["successes"] = 1
        self.save("repeat-01-c1.json", item)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_unequal_quality_decoding_rejected(self):
        quality = self.report("quality.json")
        quality["decoding"]["max_tokens"] = 64
        self.save("quality.json", quality)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_missing_p95_summary_rejected(self):
        item = self.report("sweep-summary.json")
        del item["summary"]["1"]["ttft_run_p95_ms"]
        self.save("sweep-summary.json", item)
        with self.assertRaises(ValueError):
            self.run_compare()

    def test_malformed_change_description_rejected(self):
        with self.assertRaisesRegex(ValueError, "describe"):
            compare.compare(self.manifest(self.baseline), self.manifest(self.candidate), "")

    def test_runtime_metadata_mismatch_rejected(self):
        manifest = self.report("evidence-manifest.json")
        manifest["ollama_version"] = "other-version"
        self.save("evidence-manifest.json", manifest)
        with self.assertRaises(ValueError):
            self.run_compare()


if __name__ == "__main__":
    unittest.main()
