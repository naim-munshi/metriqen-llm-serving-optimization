# Benchmark evidence report verification

The evidence manifest validator has two levels:

- `python -m tools.benchmark.evidence_gate manifest.json` checks the required
  experiment metadata and three repeated runs at each concurrency level.
- `python -m tools.benchmark.evidence_gate manifest.json --verify-reports`
  additionally opens the named benchmark JSON files and checks their schema,
  model/revision, hardware/runtime metadata, streaming mode, dataset fingerprint,
  configured concurrency, request counts, observation counts and reported failures.

Paths in `benchmark_reports[*].report_path` are resolved relative to the
manifest file's directory. Keep these reports private and under
`reports/local/`. The stricter mode rejects any run with failed requests;
preserve those failures for investigation and rerun, never delete failure
evidence or silently exclude them from comparisons.

**Important:** this is a consistency validator, not cryptographic attestation
or proof that a real LLM ran. Self-reported metadata and fabricated reports
could still pass. The quality report is also validated for matching model/revision/runtime/hardware,
request counts, and internally consistent exact-match and normalized-match scores. Human review of hardware, model digest, quality
and real execution remains mandatory.

The strict checks also reject duplicate observation indices, impossible TTFT,
non-200 successes, mismatched p50/p95 and token-coverage totals, inconsistent
workloads across runs and report paths escaping the manifest folder.
The quality evaluation dataset may differ from the benchmark workload dataset.
