
"""Cross-check generated report summaries and quality data using synthetic fixtures."""
import json
from pathlib import Path
import tempfile
import unittest

from tools.benchmark.evidence_gate import verify_report_contents


class EvidenceContentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.manifest = {
            "model_tag": "real-model-tag", "model_digest": "sha256:abcd",
            "ollama_version": "0.12.0", "hardware": "Apple Silicon",
            "benchmark_reports": [{"concurrency": 1, "report_path": "baseline.json"}],
            "quality_report": "quality.json",
        }
        self.report = {
            "schema_version": 1,
            "environment": {"model": "real-model-tag", "model_revision": "sha256:abcd",
                            "runtime_self_reported": "0.12.0",
                            "hardware_self_reported": "Apple Silicon",
                            "dataset_sha256": "a" * 64},
            "configuration": {"requests": 2, "concurrency": 1, "stream": True,
                              "warmup": 2, "max_tokens": 64, "timeout_seconds": 120},
            "results": {
                "requests": 2, "successes": 2, "failures": 0,
                "ttft_samples": 2, "completion_token_coverage": 2,
                "output_tokens": 5,
                "latency_ms": {"p50": 100, "p95": 120},
                "ttft_ms": {"p50": 20, "p95": 30},
                "observations": [
                    {"index": 0, "success": True, "status": 200, "error": None,
                     "latency_ms": 100, "ttft_ms": 20, "completion_tokens": 2},
                    {"index": 1, "success": True, "status": 200, "error": None,
                     "latency_ms": 120, "ttft_ms": 30, "completion_tokens": 3},
                ],
            },
        }
        self.quality = {
            "schema_version": 1, "model": "real-model-tag",
            "model_revision_self_reported": "sha256:abcd",
            "runtime_self_reported": "0.12.0",
            "hardware_self_reported": "Apple Silicon",
            "results": {"quality": {"sample_count": 2,
                        "exact_match": {"correct": 1, "rate": .5},
                        "normalized_exact_match": {"correct": 2, "rate": 1}},
                        "generation": {"successful_requests": 2, "failed_requests": 0}},
        }
        self.write()

    def write(self):
        (self.root / "baseline.json").write_text(json.dumps(self.report))
        (self.root / "quality.json").write_text(json.dumps(self.quality))

    def errors(self):
        return verify_report_contents(self.manifest, self.root / "manifest.json")

    def test_valid(self):
        self.assertEqual(self.errors(), [])

    def test_missing_quality(self):
        (self.root / "quality.json").unlink()
        self.assertTrue(self.errors())

    def test_quality_wrong_model(self):
        self.quality["model_revision_self_reported"] = "wrong"
        self.write()
        self.assertTrue(self.errors())

    def test_quality_inflated_score(self):
        self.quality["results"]["quality"]["exact_match"]["rate"] = .9
        self.write()
        self.assertTrue(self.errors())

    def test_duplicate_sample_index(self):
        self.report["results"]["observations"][1]["index"] = 0
        self.write()
        self.assertTrue(self.errors())

    def test_non_200_success(self):
        self.report["results"]["observations"][0]["status"] = 429
        self.write()
        self.assertTrue(self.errors())

    def test_incorrect_ttft(self):
        self.report["results"]["observations"][0]["ttft_ms"] = 200
        self.write()
        self.assertTrue(self.errors())

    def test_fake_p95(self):
        self.report["results"]["latency_ms"]["p95"] = 999
        self.write()
        self.assertTrue(self.errors())

    def test_inaccurate_token_total(self):
        self.report["results"]["output_tokens"] = 999
        self.write()
        self.assertTrue(self.errors())

    def test_wrong_request_count(self):
        self.report["results"]["requests"] = 3
        self.write()
        self.assertTrue(self.errors())

    def test_dataset_mismatch(self):
        another = json.loads(json.dumps(self.report))
        another["environment"]["dataset_sha256"] = "b" * 64
        (self.root / "other.json").write_text(json.dumps(another))
        self.manifest["benchmark_reports"].append({"concurrency": 1, "report_path": "other.json"})
        self.assertTrue(self.errors())

    def test_path_traversal(self):
        self.manifest["quality_report"] = "../outside.json"
        self.assertTrue(self.errors())

    def test_failed_requests_not_accepted(self):
        self.report["results"]["failures"] = 1
        self.report["results"]["successes"] = 1
        self.report["results"]["observations"][1].update(
            success=False, status=429, error="http_429",
            ttft_ms=None, completion_tokens=None
        )
        self.write()
        self.assertTrue(self.errors())

    def test_duplicate_report_path(self):
        self.manifest["benchmark_reports"].append(
            {"concurrency": 1, "report_path": "baseline.json"}
        )
        self.assertTrue(self.errors())


if __name__ == "__main__":
    unittest.main()
