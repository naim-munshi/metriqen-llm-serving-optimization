import json
from pathlib import Path
import tempfile
import unittest
from tools.nlp_eval import evaluator

class EvaluatorTests(unittest.TestCase):
    def test_exact_and_normalized_metric(self):
        result = evaluator.evaluate(
            {"a": "Hello World", "b": "Café", "c": "one"},
            {"a": " hello   WORLD ", "b": "Cafe\u0301", "c": "two"})
        self.assertEqual(result["exact_match"]["correct"], 0)
        self.assertEqual(result["normalized_exact_match"]["correct"], 2)
        self.assertEqual(result["sample_count"], 3)

    def test_different_ids_fail_closed(self):
        with self.assertRaises(ValueError):
            evaluator.evaluate({"a":"x"}, {"b":"x"})

    def test_no_punctuation_removal(self):
        self.assertNotEqual(evaluator.normalize("hello!"), evaluator.normalize("hello"))

    def test_duplicate_ids_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "examples.jsonl"
            path.write_text('{"id":"a","reference":"x"}\n{"id":"a","reference":"x"}\n')
            with self.assertRaises(ValueError):
                evaluator.load(path, "reference")

    def test_extra_fields_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "examples.jsonl"
            path.write_text('{"id":"a","reference":"x","prompt":"private"}\n')
            with self.assertRaises(ValueError):
                evaluator.load(path, "reference")

    def test_reports_contain_no_answer_text(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            refs, preds, output = base/"refs.jsonl", base/"preds.jsonl", base/"report.json"
            refs.write_text('{"id":"one","reference":"PRIVATE REFERENCE"}\n')
            preds.write_text('{"id":"one","prediction":"PRIVATE GENERATED CONTENT"}\n')
            self.assertEqual(evaluator.main(["--references",str(refs),"--predictions",str(preds),"--output",str(output)]), 0)
            report = output.read_text()
            self.assertNotIn("PRIVATE REFERENCE", report)
            self.assertNotIn("PRIVATE GENERATED CONTENT", report)
            self.assertEqual(json.loads(report)["results"]["sample_count"], 1)

    def test_empty_and_oversized_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"empty.jsonl"
            path.write_bytes(b"")
            with self.assertRaises(ValueError):
                evaluator.load(path, "reference")
            path.write_bytes(b"x" * (evaluator.MAX_BYTES + 1))
            with self.assertRaises(ValueError):
                evaluator.load(path, "reference")

if __name__ == "__main__":
    unittest.main()
