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
could still pass. The quality report path is currently checked for presence,
not evaluated for contents. Human review of hardware, model digest, quality
and real execution remains mandatory.
