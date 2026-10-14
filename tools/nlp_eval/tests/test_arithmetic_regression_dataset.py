"""Offline regression contract for separately versioned arithmetic quality probes.

The test verifies fixture integrity and the scoring contract using fabricated
predictions. It NEVER runs a model or asserts a real model's current accuracy.
"""
import hashlib
from pathlib import Path
import unittest

from tools.nlp_eval.evaluator import evaluate
from tools.nlp_eval.online import load_workload


DATASET = (
    Path(__file__).resolve().parents[3]
    / "docs/nlp-eval/workload.arithmetic-regression.v1.jsonl"
)


class ArithmeticRegressionFixtureTests(unittest.TestCase):
    def test_fixture_has_exact_observed_prompts_and_unambiguous_references(self):
        records, sha256 = load_workload(DATASET)
        self.assertEqual(
            [row["id"] for row in records],
            ["arith_2_plus_2", "arith_7_plus_5"],
        )
        self.assertEqual(
            [row["messages"] for row in records],
            [
                [{"role": "user", "content": "What is 2 plus 2? Answer with one number."}],
                [{"role": "user", "content": "What is 7 plus 5? Reply with only the number."}],
            ],
        )
        self.assertEqual([row["reference"] for row in records], ["4", "12"])
        self.assertEqual(hashlib.sha256(DATASET.read_bytes()).hexdigest(), sha256)

    def test_wrong_answer_stays_wrong_in_synthetic_reference_scoring(self):
        records, _ = load_workload(DATASET)
        refs = {row["id"]: row["reference"] for row in records}
        illustrative_predictions = {"arith_2_plus_2": "2", "arith_7_plus_5": "12"}
        result = evaluate(refs, illustrative_predictions)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["exact_match"]["correct"], 1)
        self.assertEqual(result["normalized_exact_match"]["correct"], 1)
        # The other model output is a fixture, not an attested inference.
        passing = evaluate(refs, {"arith_2_plus_2": "4", "arith_7_plus_5": "12"})
        self.assertEqual(passing["exact_match"]["correct"], 2)


if __name__ == "__main__":
    unittest.main()
