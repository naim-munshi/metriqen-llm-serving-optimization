"""Guard public October 20 operator-transcribed A/B/A/B arithmetic and claim scope.

These tests do NOT read private local raw evidence or attest actual Mac inference.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "docs/benchmarks/experiments/2026-10-20-ollama-parallelism-abab.json"
REPORT = BUNDLE.with_suffix(".md")
PHASES = ("A1", "B1", "A2", "B2")
FIELDS = (
    "requests_per_second_run_median", "output_tokens_per_second_run_median",
    "latency_p95_ms_run_median", "ttft_p95_ms_run_median",
)
COMPARISONS = {
    "A1_to_B1": ("A1", "B1"),
    "A2_to_B2": ("A2", "B2"),
    "A1_to_A2_drift": ("A1", "A2"),
    "B1_to_B2_drift": ("B1", "B2"),
}


class OperatorTranscribedAbabAggregateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_json = BUNDLE.read_text(encoding="utf-8")
        cls.json = json.loads(cls.raw_json)
        cls.md = REPORT.read_text(encoding="utf-8")

    def test_provenance_and_security_boundary(self):
        record = self.json
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["kind"], "operator_transcribed_local_abab_aggregate")
        self.assertEqual(record["experiment_date"], "2026-10-20")
        p = record["provenance"]
        self.assertEqual(p["classification"], "descriptive_replication_only")
        self.assertTrue(p["private_four_phase_consistency_passed"])
        for key in (
            "github_ci_revalidates_actual_mac_execution",
            "independent_active_slots_or_power_attested",
            "causal_speedup_verified",
            "p2_portfolio_human_accepted",
        ):
            self.assertFalse(p[key], key)
        for text in (self.raw_json, self.md):
            for forbidden in (
                "/Users/", "Authorization: Bearer", "api_key", "secret_key",
                "-----BEGIN PRIVATE KEY-----", "reports/local/abab-",
            ):
                self.assertNotIn(forbidden, text)
        self.assertIn("not causal", self.md)
        self.assertIn("not independently", self.md)

    def test_matched_model_and_complete_counts(self):
        self.assertEqual(self.json["model"]["tag"], "llama3.2:1b")
        self.assertEqual(len(self.json["model"]["digest"]), 64)
        self.assertEqual(self.json["model"]["quantization"], "Q8_0")
        method = self.json["methodology"]
        self.assertEqual(method["phases"], {"A1": 1, "B1": 2, "A2": 1, "B2": 2})
        self.assertEqual(method["offered_concurrency"], [1, 2, 4])
        self.assertEqual(method["repeats_per_concurrency_per_phase"], 3)
        self.assertEqual(method["measured_requests_per_run"], 36)
        self.assertEqual(method["successful_measured_requests"], 4 * 3 * 3 * 36)
        self.assertEqual(method["failed_measured_requests"], 0)
        self.assertEqual(method["quality_samples_per_phase"], 36)
        for dataset_key in ("benchmark_dataset_sha256", "quality_dataset_sha256"):
            self.assertEqual(len(method[dataset_key]), 64)
        for phase in PHASES:
            self.assertAlmostEqual(self.json["quality"]["exact_match_per_phase"][phase], 31 / 36, places=4)
            self.assertAlmostEqual(self.json["quality"]["normalized_exact_match_per_phase"][phase], 33 / 36, places=4)

    def test_all_concurrency_metrics_and_markdown_values(self):
        groups = self.json["metrics_by_concurrency"]
        self.assertEqual(set(groups), {"1", "2", "4"})
        for offered, group in groups.items():
            self.assertEqual(set(group["phase_medians"]), set(PHASES))
            self.assertEqual(set(group["throughput_delta_pct_reported"]), set(COMPARISONS))
            for phase in PHASES:
                values = group["phase_medians"][phase]
                self.assertEqual(set(values), set(FIELDS))
                self.assertTrue(all(isinstance(x, (int, float)) and x > 0 for x in values.values()))
                for metric, label in (
                    ("requests_per_second_run_median", "Successful requests/s"),
                    ("output_tokens_per_second_run_median", "Output tokens/s"),
                    ("latency_p95_ms_run_median", "Successful latency p95 (ms)"),
                    ("ttft_p95_ms_run_median", "TTFT p95 (ms)"),
                ):
                    row = next(x for x in self.md.splitlines() if x.startswith(f"| {offered} | {label} |"))
                    self.assertIn(f"{values[metric]:.3f}", row)
            for key, (before, after) in COMPARISONS.items():
                # Public medians are rounded to 3 decimals while source deltas
                # were calculated from full precision. Tolerate that rounding,
                # never silently recalculate or replace raw source percentages.
                medians = group["phase_medians"]
                rps_before = medians[before]["requests_per_second_run_median"]
                rps_after = medians[after]["requests_per_second_run_median"]
                approx = 100 * (rps_after / rps_before - 1)
                self.assertLess(abs(group["throughput_delta_pct_reported"][key] - approx), 0.9)

    def test_negative_findings_are_not_cherry_picked(self):
        groups = self.json["metrics_by_concurrency"]
        self.assertLess(groups["1"]["throughput_delta_pct_reported"]["A2_to_B2"], 0)
        self.assertLess(groups["2"]["throughput_delta_pct_reported"]["A2_to_B2"], 0)
        self.assertLess(groups["2"]["throughput_delta_pct_reported"]["B1_to_B2_drift"], -40)
        self.assertIn("48.69%", self.md)
        self.assertIn("uninterrupted AC", self.md)
        self.assertIn("B1", self.md)
        self.assertIn("A2", self.md)


if __name__ == "__main__":
    unittest.main()
