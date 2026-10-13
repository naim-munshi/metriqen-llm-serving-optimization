# Controlled local serving configuration comparisons

This workflow compares **two separately measured local sweeps**, not a
synthetic CI speedup. It is for the **same pinned model weights, hardware,
runtime/version, quantization, workload, and decoding configuration**.
Only **one explicitly documented serving setting** should change.
This first comparator intentionally rejects different model digests,
quantization, hardware, runtime versions and workload fingerprints.
Those comparisons require a different, more careful protocol.

## 1. Collect real baseline and candidate evidence

For **each** experiment, with Ollama and the loopback Go gateway running:

1. Record model tag/digest, runtime version, OS, hardware/RAM, quantization,
   thermal/power state and background load. Keep the same prompts and decoding.
2. Run `python3 -m tools.benchmark.sweep` with a **different unused**
   `--output-dir`, e.g. `reports/local/baseline-01` versus
   `reports/local/candidate-01`. Use the exact same model digest, dataset,
   warmup, output cap, and 1/2/4 concurrency rotation. Prefer at least
   `--requests 30 --repeats 3` and >=30 diverse quality references.
3. Run `python3 -m tools.nlp_eval.online` for each condition with matching
   model/runtime/hardware, same reference dataset and token cap. Store
   `quality.json` in each corresponding sweep directory.
4. Review actual model execution, then run
   `python3 -m tools.benchmark.evidence_bundle` in each experiment directory.
   Follow [the exact command and human-attestation caveats](real-model-evidence-bundle.md).
5. Preserve all raw reports and record the **one** setting changed. Do not
   discard or replace a failed run to make a favorable comparison.

Never commit unpublished local reports or attach secret-bearing logs.

## 2. Compare only matched evidence

After both directories each contain `evidence-manifest.json`,
`quality.json`, `sweep-summary.json` and all nine run reports:

~~~sh
python3 -m tools.benchmark.compare \
  --baseline reports/local/baseline-01/evidence-manifest.json \
  --candidate reports/local/candidate-01/evidence-manifest.json \
  --change 'Describe exactly one operator-controlled serving setting change' \
  --output reports/local/descriptive-serving-comparison-01.json
~~~

The CLI refuses mismatched model digests, runtime, hardware, host OS,
quantization, quality and benchmark datasets, generation settings, sample
counts or inaccurate run summaries. It independently revalidates every raw
report through the current strict evidence gate, and refuses output overwrite.

For each concurrency it reports run-level median throughput (requests/sec,
output tokens/sec when usage is complete), latency p50/p95 and TTFT p50/p95,
alongside relative percentage change. Throughput higher is preferable;
latency/TTFT lower is preferable. A delta is **not proof of a causal speedup**
and run-level median deltas are **not confidence intervals**.

Interpretation is automatically restricted:

- `quality_regression_not_eligible`: exact or normalized-reference agreement
  falls below baseline; no positive optimization conclusion is eligible.
- `exploratory_small_sample`: fewer than 30 quality references or fewer
  than 30 requests per run; preliminary investigation only.
- `descriptive_missing_token_usage`: insufficient output-token metadata
  prevents token-throughput comparison. No values are estimated.
- `descriptive_not_causal`: matched metadata, adequate minimum counts,
  unchanged reference agreement and complete token metadata. The result
  remains descriptive and cannot establish cause, statistical significance
  or production SLOs by itself.

Legacy example datasets contain **three performance prompts and two quality questions**. Versioned [v1 synthetic starter workloads](workload-design-v1.md) contain 36 technical prompts and 36 simple reference questions. Neither set establishes representative latency or semantic quality. Use the same dataset bytes for baseline and candidate, and choose a reviewed workload that matches your intended use case before publishing a case study.

## 3. Recruiter-ready evidence

Present the exact hardware, model artifact/revision and runtime, comparison
protocol, full baseline/candidate results, failure counts, quality gate,
latency and throughput charts, uncertainty and one configuration difference.
Link the code and CI run, while clearly distinguishing CI test fixtures
from measured local inference. Do not invent GPU utilization, cost, vLLM
runtime results, deployment or performance improvements.

This comparator does not model thermal drift, OS scheduling, KV-cache state,
sampling uncertainty or hidden serving parameters. Human attestation is not
cryptographic verification. Follow an A/B/A design with repetitions,
workload buckets and statistical uncertainty estimates before making a
stronger engineering claim.
