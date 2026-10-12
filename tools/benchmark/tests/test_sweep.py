"""Offline deterministic checks for repeated benchmark orchestration."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.benchmark import sweep


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dataset = self.root / "prompts.jsonl"
        self.dataset.write_text('{"messages":[{"role":"user","content":"PRIVATE_PROMPT"}]}\n')
        self.args = argparse.Namespace(
            endpoint="http://127.0.0.1:8081/v1/chat/completions",
            model="llama3.2:1b", model_revision="a" * 64,
            runtime="Ollama local", hardware="Apple M2 local",
            dataset=self.dataset, output_dir=self.root / "sweep",
            repeats=3, requests=2, warmup=1, max_tokens=32,
            timeout=120, no_stream_usage=False,
        )
        self.calls = []

    def write_fixture(self, arguments, *, no_usage=False, failure=False):
        values = [item for item in arguments if item not in ("--stream", "--no-stream-usage")]
        data = dict(zip(values[::2], values[1::2]))
        self.calls.append(data)
        path = Path(data["--output"])
        concurrency = int(data["--concurrency"])
        count = int(data["--requests"])
        succeeded = count - int(failure)
        usage_requested = "--no-stream-usage" not in arguments
        tokens = 3 if not no_usage and usage_requested else None
        report = {
            "schema_version": 1,
            "configuration": {
                "requests": count, "concurrency": concurrency,
                "warmup": int(data["--warmup"]), "stream": True,
                "stream_usage_requested": usage_requested,
                "max_tokens": int(data["--max-tokens"]),
                "timeout_seconds": float(data["--timeout"]),
            },
            "environment": {
                "endpoint": data["--endpoint"], "model": data["--model"],
                "model_revision": data["--model-revision"],
                "runtime_self_reported": data["--runtime"],
                "hardware_self_reported": data["--hardware"],
                "dataset_sha256": __import__("hashlib").sha256(self.dataset.read_bytes()).hexdigest(),
            },
            "results": {
                "requests": count, "successes": succeeded,
                "failures": int(failure),
                "requests_per_second": concurrency * 0.1,
                "wall_seconds": 10,
                "latency_ms": {"p50": 1000 / concurrency, "p95": 2000 / concurrency},
                "ttft_ms": {"p50": 250 / concurrency, "p95": 500 / concurrency},
                "completion_token_coverage": succeeded if tokens is not None else 0,
                "output_tokens": succeeded * tokens if tokens is not None else None,
                "output_tokens_per_second": succeeded * tokens / 10 if tokens is not None else None,
                "observations": [
                    {"index": i, "success": i < succeeded, "status": 200 if i < succeeded else 429}
                    for i in range(count)
                ],
            },
        }
        path.write_text(json.dumps(report))
        return 1 if failure else 0

    def test_rotating_order_is_balanced(self):
        self.assertEqual(sweep.run_order(3), [
            (1, 1), (1, 2), (1, 4),
            (2, 2), (2, 4), (2, 1),
            (3, 4), (3, 1), (3, 2),
        ])

    def test_full_nine_run_sweep_preserves_individual_evidence(self):
        with patch.object(sweep.runner, "main", side_effect=self.write_fixture):
            with redirect_stdout(StringIO()):
                summary_path = sweep.run(self.args)
        self.assertEqual(len(self.calls), 9)
        self.assertEqual(len(list(self.args.output_dir.glob("repeat-*.json"))), 9)
        summary_text = summary_path.read_text()
        self.assertNotIn("PRIVATE_PROMPT", summary_text)
        report = json.loads(summary_text)
        self.assertEqual(report["environment"]["model_revision"], "a" * 64)
        self.assertEqual(len(report["run_files"]), 9)
        self.assertEqual(report["summary"]["1"]["runs"], 3)
        self.assertEqual(report["summary"]["4"]["successful_requests"], 6)
        self.assertEqual(report["summary"]["2"]["output_tokens_per_second"]["median"], 0.6)

    def test_missing_stream_usage_never_invents_token_throughput(self):
        with patch.object(sweep.runner, "main", side_effect=lambda argv: self.write_fixture(argv, no_usage=True)):
            with redirect_stdout(StringIO()):
                summary = sweep.run(self.args)
        result = json.loads(summary.read_text())
        for label in ("1", "2", "4"):
            self.assertIsNone(result["summary"][label]["output_tokens_per_second"])
            self.assertEqual(result["summary"][label]["completion_token_coverage"], 0)

    def test_failed_run_retains_partial_reports_and_does_not_write_summary(self):
        def sometimes_fail(argv):
            return self.write_fixture(argv, failure=len(self.calls) == 1)
        with patch.object(sweep.runner, "main", side_effect=sometimes_fail):
            with redirect_stdout(StringIO()):
                with self.assertRaisesRegex(RuntimeError, "partial reports retained"):
                    sweep.run(self.args)
        self.assertTrue((self.args.output_dir / "repeat-01-c1.json").exists())
        self.assertTrue((self.args.output_dir / "repeat-01-c2.json").exists())
        self.assertFalse((self.args.output_dir / "sweep-summary.json").exists())

    def test_existing_output_directory_is_never_overwritten(self):
        self.args.output_dir.mkdir()
        (self.args.output_dir / "prior.json").write_text("keep me")
        with self.assertRaisesRegex(ValueError, "already exists"):
            sweep.run(self.args)
        self.assertEqual((self.args.output_dir / "prior.json").read_text(), "keep me")

    def test_invalid_digest_prevents_any_request(self):
        self.args.model_revision = "llama3.2:1b"
        with patch.object(sweep.runner, "main") as mocked:
            with self.assertRaisesRegex(ValueError, "64-character"):
                sweep.run(self.args)
            mocked.assert_not_called()
        self.assertFalse(self.args.output_dir.exists())

    def test_mismatched_dataset_fingerprint_causes_no_aggregate(self):
        def inconsistent(argv):
            rc = self.write_fixture(argv)
            values = [item for item in argv if item not in ("--stream", "--no-stream-usage")]
            p = Path(dict(zip(values[::2], values[1::2]))["--output"])
            item = json.loads(p.read_text())
            item["environment"]["dataset_sha256"] = "b" * 64
            p.write_text(json.dumps(item))
            return rc
        with patch.object(sweep.runner, "main", side_effect=inconsistent):
            with redirect_stdout(StringIO()):
                with self.assertRaisesRegex(RuntimeError, "partial reports retained"):
                    sweep.run(self.args)
        self.assertFalse((self.args.output_dir / "sweep-summary.json").exists())

    def test_opt_out_usage_preserved_in_summary(self):
        self.args.no_stream_usage = True
        with patch.object(sweep.runner, "main", side_effect=self.write_fixture):
            with redirect_stdout(StringIO()):
                summary = sweep.run(self.args)
        record = json.loads(summary.read_text())
        self.assertFalse(record["configuration"]["stream_usage_requested"])
        self.assertEqual(record["summary"]["1"]["completion_token_coverage"], 0)
        self.assertIsNone(record["summary"]["1"]["output_tokens_per_second"])


if __name__ == "__main__":
    unittest.main()
