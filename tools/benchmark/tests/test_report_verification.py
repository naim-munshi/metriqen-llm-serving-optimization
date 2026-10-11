import json
from pathlib import Path
import tempfile
import unittest
from tools.benchmark.evidence_gate import verify_report_contents

class ReportVerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest_path = self.root / "manifest.json"
        self.report = {
            "schema_version": 1,
            "configuration": {"requests": 2, "concurrency": 1, "stream": True},
            "environment": {"model": "model", "model_revision": "digest",
                "runtime_self_reported": "runtime", "hardware_self_reported": "hardware",
                "dataset_sha256": "a" * 64},
            "results": {"requests": 2, "successes": 2, "failures": 0,
                "observations": [{"success": True}, {"success": True}]}
        }
        self.manifest = {
            "model_tag": "model", "model_digest": "digest", "ollama_version": "runtime",
            "hardware": "hardware", "benchmark_reports": [
                {"concurrency": 1, "report_path": "run.json"}
            ]
        }
    def save(self):
        (self.root / "run.json").write_text(json.dumps(self.report))
    def test_valid_report(self):
        self.save()
        self.assertEqual(verify_report_contents(self.manifest, self.manifest_path), [])
    def test_missing_file(self):
        self.assertTrue(verify_report_contents(self.manifest, self.manifest_path))
    def test_failed_requests_not_accepted(self):
        self.report["results"].update(successes=1, failures=1,
            observations=[{"success": True}, {"success": False}])
        self.save()
        self.assertIn("failed requests", str(verify_report_contents(self.manifest, self.manifest_path)))
    def test_wrong_model_revision(self):
        self.report["environment"]["model_revision"] = "wrong"
        self.save()
        self.assertTrue(verify_report_contents(self.manifest, self.manifest_path))
    def test_dataset_mismatch(self):
        self.save()
        second = json.loads(json.dumps(self.report))
        second["environment"]["dataset_sha256"] = "b" * 64
        (self.root / "run2.json").write_text(json.dumps(second))
        self.manifest["benchmark_reports"].append({"concurrency": 1, "report_path": "run2.json"})
        self.assertTrue(verify_report_contents(self.manifest, self.manifest_path))
    def test_observation_mismatch(self):
        self.report["results"]["observations"] = []
        self.save()
        self.assertTrue(verify_report_contents(self.manifest, self.manifest_path))
    def test_duplicate_path(self):
        self.save()
        self.manifest["benchmark_reports"].append({"concurrency": 1, "report_path": "run.json"})
        self.assertTrue(verify_report_contents(self.manifest, self.manifest_path))

if __name__ == "__main__":
    unittest.main()
