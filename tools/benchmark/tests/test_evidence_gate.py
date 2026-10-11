import unittest
from tools.benchmark.evidence_gate import validate

def manifest():
    return {
        "model_tag":"llama3.2:1b", "model_digest":"sha256:abc123",
        "ollama_version":"0.12.1", "host_os":"macOS",
        "hardware":"Apple M1 8GB", "quantization":"Q4_K_M",
        "timestamp_utc":"2026-10-12T12:00:00Z", "notes":"Local measurements",
        "real_model_executed":True,
        "benchmark_reports":[{"concurrency":c,"report_path":f"reports/local/c{c}-{i}.json"}
                             for c in (1,2,4) for i in range(3)],
        "quality_report":"reports/local/quality.json",
    }

class EvidenceGateTests(unittest.TestCase):
    def test_complete_manifest(self):
        self.assertEqual(validate(manifest()), [])
    def test_unexecuted_is_rejected(self):
        m=manifest();m["real_model_executed"]=False
        self.assertTrue(any("real_model_executed" in x for x in validate(m)))
    def test_missing_repetition_is_rejected(self):
        m=manifest();m["benchmark_reports"].pop()
        self.assertTrue(validate(m))
    def test_placeholder_is_rejected(self):
        m=manifest();m["model_digest"]="REPLACE_WITH_DIGEST"
        self.assertTrue(any("model_digest" in x for x in validate(m)))
    def test_invalid_type_is_rejected(self):
        self.assertTrue(validate([]))
    def test_boolean_concurrency_is_rejected(self):
        m=manifest();m["benchmark_reports"][0]["concurrency"]=True
        self.assertTrue(validate(m))

if __name__=="__main__":
    unittest.main()
