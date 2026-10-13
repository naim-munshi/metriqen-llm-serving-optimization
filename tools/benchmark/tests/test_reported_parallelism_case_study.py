"""Arithmetic/provenance regression checks for a developer-reported case study.

These tests do NOT load original locally-held inference reports, run Ollama,
or independently attest the authenticity of real-model measurements.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

from tools.benchmark.compare import _percent_change


ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "docs/benchmarks/experiments/2026-10-13-ollama-parallelism.json"
STUDY = BUNDLE.with_suffix(".md")
FIELDS = {
    "requests_per_second", "latency_run_p95_ms",
    "ttft_run_p50_ms", "output_tokens_per_second",
}


class DeveloperReportedParallelismCaseStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = BUNDLE.read_text(encoding="utf-8")
        cls.report = json.loads(cls.raw)
        cls.markdown = STUDY.read_text(encoding="utf-8")

    def test_explicit_provenance_does_not_pretend_ci_verified_hardware(self):
        p = self.report["provenance"]
        self.assertEqual(self.report["schema_version"], 1)
        self.assertEqual(self.report["kind"], "developer_transcribed_real_local_aggregate")
        self.assertEqual(self.report["experiment_date"], "2026-10-13")
        self.assertEqual(p["comparator_status"], "descriptive_not_causal")
        self.assertIs(p["private_evidence_verified_locally_by_operator"], True)
        self.assertIs(p["github_ci_revalidates_raw_hardware_reports"], False)
        self.assertIn("do not independently prove", p["disclaimer"])
        self.assertIn("not a verified causal speedup", self.markdown)
        self.assertIn("CI", self.markdown)

    def test_model_and_protocol_fixed_to_operator_reported_configuration(self):
        data = self.report
        self.assertEqual(data["model"]["tag"], "llama3.2:1b")
        self.assertEqual(
            data["model"]["digest"],
            "baf6a787fdffd633537aa2eb51cfd54cb93ff08e28040095462bb63daf552878",
        )
        self.assertEqual(data["model"]["quantization"], "Q8_0")
        self.assertEqual(data["environment"]["runtime"], "Ollama 0.30.8")
        config = data["methodology"]
        self.assertEqual(config["baseline_ollama_num_parallel"], 1)
        self.assertEqual(config["candidate_ollama_num_parallel"], 2)
        self.assertEqual(config["concurrency_levels"], [1, 2, 4])
        self.assertEqual(config["repeats_per_concurrency"], 3)
        self.assertEqual(config["measured_requests_per_run"], 36)
        self.assertEqual(config["warmup_requests_per_run"], 2)
        self.assertEqual(config["temperature"], 0)
        self.assertEqual(config["max_completion_tokens"], 128)
        self.assertTrue(config["streaming"])
        self.assertEqual(config["total_measured_requests_across_both_configurations"], 648)
        self.assertEqual(config["requests_per_configuration"], 324)
        for source in ("benchmark_dataset", "quality_dataset"):
            self.assertTrue((ROOT / config[source]).is_file())

    def test_no_hidden_failures_and_matching_concurrency_coverage(self):
        groups = self.report["metrics_by_concurrency"]
        self.assertEqual(set(groups), {"1", "2", "4"})
        for key in ("1", "2", "4"):
            group = groups[key]
            self.assertEqual(group["successful_requests_per_configuration"], 108)
            self.assertEqual(group["failed_requests_per_configuration"], 0)
            self.assertEqual(set(group["baseline"]), FIELDS)
            self.assertEqual(set(group["candidate"]), FIELDS)
            for config in ("baseline", "candidate"):
                for metric in group[config].values():
                    self.assertIs(type(metric), float)
                    self.assertTrue(math.isfinite(metric) and metric > 0)

    def test_every_published_delta_matches_strict_compare_formula(self):
        for group in self.report["metrics_by_concurrency"].values():
            self.assertEqual(set(group["percentage_change"]), FIELDS)
            for metric in FIELDS:
                before = group["baseline"][metric]
                after = group["candidate"][metric]
                expected = _percent_change(before, after)
                self.assertIsNotNone(expected)
                self.assertAlmostEqual(group["percentage_change"][metric], expected, places=3)

    def test_quality_accounting_and_non_regression_are_accurate(self):
        quality = self.report["quality"]
        for name in ("baseline", "candidate"):
            item = quality[name]
            self.assertEqual(item["samples"], 36)
            self.assertEqual(item["successful_requests"], 36)
            self.assertEqual(item["failed_requests"], 0)
            self.assertEqual(item["exact_correct"], 31)
            self.assertEqual(item["normalized_exact_correct"], 33)
            self.assertGreaterEqual(item["normalized_exact_correct"], item["exact_correct"])
        self.assertEqual(quality["baseline"], quality["candidate"])
        self.assertIn("not semantic", quality["claim"])
        self.assertIn("31/36", self.markdown)
        self.assertIn("33/36", self.markdown)

    def test_public_record_exposes_regressions_not_only_improvements(self):
        groups = self.report["metrics_by_concurrency"]
        self.assertGreater(groups["1"]["percentage_change"]["ttft_run_p50_ms"], 0)
        self.assertLess(groups["1"]["percentage_change"]["requests_per_second"], 0)
        self.assertLess(groups["2"]["percentage_change"]["ttft_run_p50_ms"], 0)
        self.assertGreater(groups["2"]["percentage_change"]["requests_per_second"], 0)
        self.assertIn("+27.025%", self.markdown)
        self.assertIn("−88.991%", self.markdown)
        self.assertIn("+34.235%", self.markdown)

    def test_public_json_has_no_private_request_payloads(self):
        for forbidden in ("/Users/", "PRIVATE_PROMPT", "Authorization:", "api_key",
                          "observations", "messages", "predictions"):
            self.assertNotIn(forbidden, self.raw)
        self.assertIn("reports/local/", self.raw)
        self.assertNotIn("real-model-evidence-manifest.json", self.raw)

    def test_public_markdown_table_tracks_json_aggregates(self):
        for concurrency, group in self.report["metrics_by_concurrency"].items():
            self.assertIn(f"| {concurrency} | Successful requests/s |", self.markdown)
            self.assertIn(f"| {concurrency} | Output tokens/s |", self.markdown)
            for key, expected in group["percentage_change"].items():
                val = f"{expected:+.3f}%" if expected > 0 else f"{expected:.3f}%"
                # Published table stores signed percentages to three decimals.
                self.assertIn(val, self.markdown)
        self.assertIn("run-level", self.markdown)
        self.assertIn("not an independently", self.markdown)


if __name__ == "__main__":
    unittest.main()
