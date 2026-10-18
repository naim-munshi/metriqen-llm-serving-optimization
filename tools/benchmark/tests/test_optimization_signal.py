"""Synthetic fixtures only; a classification does not attest actual LLM speedup."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest

from tools.benchmark import optimization_signal as signal
from tools.benchmark.tests.test_compare import ComparisonTests


class OptimizationSignalTests(unittest.TestCase):
    def setUp(self):
        example = ComparisonTests(methodName="test_valid_results_are_labeled_exploratory_not_speedups")
        example.setUp()
        self.addCleanup(example.doCleanups)
        self.e = example
        self.a = example.manifest(example.baseline)
        self.b = example.manifest(example.candidate)

    def review(self, **kwargs):
        return signal.review(self.a, self.b, "Optimized one Go gateway path",
                             min_quality_samples=1, min_requests_per_run=1, **kwargs)

    def test_all_concurrency_results_and_true_limitations_present(self):
        result = self.review()
        self.assertEqual(result["classification"], "descriptive_guardrails_met_not_causal")
        self.assertEqual(result["target_concurrency"], 2)
        self.assertTrue(result["quality_non_regression"])
        self.assertTrue(result["raw_evidence_eligible"])
        self.assertEqual(set(result["metric_delta_pct_by_concurrency"]), {"1","2","4"})
        self.assertEqual(result["tradeoff_flags"], [])
        self.assertFalse(result["causality_verified"])
        self.assertFalse(result["gpu_or_runtime_verified"])
        raw = json.dumps(result)
        for forbidden in ("PRIVATE_PROMPT","secret-access","synthetic reply",
                          str(self.e.root),"model_digest","128.0.0.1"):
            self.assertNotIn(forbidden, raw)

    def test_default_minimum_sample_requirement_never_passes_tiny_fixture(self):
        result = signal.review(self.a, self.b, "Changed Go gateway path")
        self.assertEqual(result["classification"], "insufficient_or_ineligible_evidence")
        self.assertEqual(result["comparison_status"], "exploratory_small_sample")
        self.assertFalse(result["raw_evidence_eligible"])

    def test_quality_regression_blocks_optimization_even_with_better_rps(self):
        report = self.e.report("quality.json")
        report["results"]["quality"]["exact_match"] = {"correct":1,"rate":0.5}
        report["results"]["quality"]["normalized_exact_match"] = {"correct":1,"rate":0.5}
        self.e.save("quality.json", report)
        result = self.review()
        self.assertEqual(result["classification"],"insufficient_or_ineligible_evidence")
        self.assertFalse(result["quality_non_regression"])

    def test_missing_token_usage_cannot_claim_full_coverage(self):
        self.e.write_bundle(self.e.candidate, wall=0.5, completion_usage=False)
        result = self.review()
        self.assertEqual(result["comparison_status"],"descriptive_missing_token_usage")
        self.assertEqual(result["classification"],"insufficient_or_ineligible_evidence")

    def test_all_concurrency_downside_is_always_visible(self):
        self.e.write_bundle(self.e.candidate, wall=1.5)
        result = self.review()
        self.assertEqual(result["classification"],"candidate_has_material_tradeoffs")
        self.assertTrue(any("regression" in x for x in result["tradeoff_flags"]))
        self.assertIn("1", result["metric_delta_pct_by_concurrency"])
        self.assertIn("2", result["metric_delta_pct_by_concurrency"])
        self.assertIn("4", result["metric_delta_pct_by_concurrency"])

    def test_zero_improvement_cannot_be_accepted_as_a_win(self):
        self.e.write_bundle(self.e.candidate, wall=1.0)
        result = self.review()
        self.assertEqual(result["classification"],"target_gain_not_demonstrated")

    def test_predeclared_gain_and_regression_thresholds(self):
        self.assertEqual(self.review(min_rps_gain=5)["classification"],
                         "descriptive_guardrails_met_not_causal")
        self.assertEqual(self.review(min_rps_gain=100)["classification"],
                         "target_gain_not_demonstrated")
        for invalid in (None, True, "5", -1, 0, 101, float("nan"), float("inf")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.review(min_rps_gain=invalid)
        for invalid in (-1, 101, True, float("nan"), float("inf")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.review(max_regression=invalid)
        for invalid in (0, 3, "2", True, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.review(target=invalid)

    def test_raw_model_digest_or_percentile_tampering_fails_closed(self):
        m = self.e.report("evidence-manifest.json")
        m["model_digest"] = "f"*64
        self.e.save("evidence-manifest.json", m)
        with self.assertRaises(ValueError):
            self.review()
        m["model_digest"] = "a"*64
        self.e.save("evidence-manifest.json", m)
        raw = self.e.report("repeat-01-c2.json")
        raw["results"]["requests_per_second"] = 9999
        self.e.save("repeat-01-c2.json", raw)
        with self.assertRaises(ValueError):
            self.review()

    def test_cli_writes_one_sanitized_report_without_overwriting(self):
        path = self.e.root / "comparison-signal.json"
        args = ["--baseline",str(self.a),"--candidate",str(self.b),
                "--change","Optimized one Go gateway path","--output",str(path)]
        with redirect_stdout(StringIO()),redirect_stderr(StringIO()):
            self.assertEqual(signal.main(args),2)  # tiny samples cannot pass
        content = path.read_text()
        self.assertIn('"classification": "insufficient_or_ineligible_evidence"',content)
        self.assertNotIn(str(self.e.root), content)
        err=StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(err):
            self.assertEqual(signal.main(args),2)
        self.assertIn("invalid or ineligible",err.getvalue())
        self.assertEqual(path.read_text(),content)
        self.assertFalse((self.e.root / "never-created.json").exists())


if __name__ == "__main__":
    unittest.main()
