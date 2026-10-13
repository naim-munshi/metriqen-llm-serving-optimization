"""Versioned synthetic workload contract: test shapes, not model quality."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from tools.benchmark import runner
from tools.nlp_eval import online


ROOT = Path(__file__).resolve().parents[3]
PERFORMANCE = ROOT / "docs/benchmarks/workload.v1.jsonl"
QUALITY = ROOT / "docs/nlp-eval/workload.v1.jsonl"


class VersionedWorkloadTests(unittest.TestCase):
    def test_performance_workload_loads_without_schema_violations(self):
        messages, fingerprint = runner.read_dataset(PERFORMANCE)
        self.assertEqual(len(messages), 36)
        self.assertEqual(len(fingerprint), 64)
        for item in messages:
            self.assertEqual(len(item), 1)
            self.assertEqual(item[0]["role"], "user")
            self.assertGreater(len(item[0]["content"]), 40)
            self.assertNotIn("PRIVATE_PROMPT", item[0]["content"])

    def test_performance_char_length_buckets_are_explicit_not_token_estimates(self):
        messages, _ = runner.read_dataset(PERFORMANCE)
        counts = [len(m[0]["content"]) for m in messages]
        self.assertTrue(all(45 <= n <= 160 for n in counts[:12]))
        self.assertTrue(all(250 <= n <= 700 for n in counts[12:24]))
        self.assertTrue(all(850 <= n <= 2000 for n in counts[24:]))
        self.assertGreater(min(counts[24:]), max(counts[12:24]))

    def test_quality_workload_has_unique_reviewable_reference_examples(self):
        rows, fingerprint = online.load_workload(QUALITY)
        self.assertEqual(len(rows), 36)
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual([r["id"] for r in rows],
                         [f"q{i:02d}" for i in range(1, 37)])
        self.assertEqual(rows[0]["reference"], "Paris")
        self.assertEqual(rows[12]["reference"], "15")
        self.assertEqual(rows[24]["reference"], "Central Processing Unit")
        for item in rows:
            self.assertTrue(item["reference"])
            self.assertEqual(len(item["messages"]), 1)
            self.assertIn("without any extra words or punctuation",
                          item["messages"][0]["content"])

    def test_v1_inputs_are_not_mutations_of_prior_example_datasets(self):
        _, new_benchmark_hash = runner.read_dataset(PERFORMANCE)
        _, old_benchmark_hash = runner.read_dataset(
            ROOT / "docs/benchmarks/workload.example.jsonl"
        )
        _, new_quality_hash = online.load_workload(QUALITY)
        _, old_quality_hash = online.load_workload(
            ROOT / "docs/nlp-eval/workload.example.jsonl"
        )
        self.assertNotEqual(new_benchmark_hash, old_benchmark_hash)
        self.assertNotEqual(new_quality_hash, old_quality_hash)

    def test_raw_quality_jsonl_only_has_documented_fields(self):
        seen = set()
        for line in QUALITY.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            self.assertEqual(set(item), {"id", "messages", "reference"})
            self.assertNotIn(item["id"], seen)
            seen.add(item["id"])
        self.assertEqual(len(seen), 36)


if __name__ == "__main__":
    unittest.main()
