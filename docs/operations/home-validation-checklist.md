# Phone-first preparation and home validation

No Ollama execution is required for this preparation phase. The
`evidence_gate.py` tool checks that a completed experiment manifest has
required fields and enough runs. **With `--verify-reports`, it checks referenced local report schema,
metrics and quality consistency. It does not independently authenticate
hardware, prove genuine execution or establish causality.** Never set
`real_model_executed` to true until you have actually run the model.

## Before going home

- Review the [baseline protocol](real-model-baseline.md).
- Review the [Ollama compatibility matrix](ollama-compatibility.md).
- Confirm the target model and workload datasets are appropriate.
- Keep the local gateway on loopback and never commit prompts or raw responses.

## At home on your Mac

1. Start Ollama and check the model is installed and actually generates text.
2. Record real OS, model digest, Ollama version, hardware and quantization.
3. Start the Go gateway and execute the protocol's three repeated runs at
   concurrency 1, 2 and 4, plus the separate NLP quality evaluation.
4. Keep report artifacts under `reports/local/` and create a private
   manifest based on the schema below. This is a *template*, not evidence.
5. Run `python -m tools.benchmark.evidence_gate path/to/manifest.json --verify-reports`.
   A pass checks report consistency, not independent model/hardware authenticity; review raw measurements manually.

## Manifest fields

Required strings: `model_tag`, `model_digest`, `ollama_version`,
`host_os`, `hardware`, `quantization`, `timestamp_utc`, `notes`.

Required boolean: `real_model_executed`.

Required report paths: `quality_report` and `benchmark_reports` containing
at least three entries each for concurrency 1, 2 and 4. Each entry contains
`concurrency` (integer) and `report_path` (string).

All metadata is self-reported. In `--verify-reports` mode the validator
opens and checks the referenced local benchmark and quality reports;
it cannot independently authenticate their origin. Do not publish performance claims
solely because the validator exits successfully.
