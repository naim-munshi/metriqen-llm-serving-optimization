# Portfolio P3 — Conservative offline optimization signal

This instrument is a **tradeoff screen**, not an attestation or public
performance claim. It revalidates both private, operator-reported raw sweep
bundles with the existing strict [matched-experiment comparator](controlled-comparison.md)
before producing any result. Only a real Mac/Ollama operator run can produce
real throughput and latency evidence; **GitHub CI uses deterministic synthetic
fixtures**.

## Run after a matched real-model baseline/candidate experiment

Use the same pinned model digest, runtime version, quantization, dataset
SHA-256, concurrency rotation, max output cap, timeout, response format,
hardware and quality dataset on both conditions. Document the **one
Metriqen-controlled code or serving-path change** in the PR and capture its
exact Git revision or diff. This CLI cannot verify the declared change
against version control, model weights, CPU architecture, GPU/Metal runtime
or physical hardware.

```sh
python3 -m tools.benchmark.optimization_signal \
  --baseline reports/local/baseline/evidence-manifest.json \
  --candidate reports/local/candidate/evidence-manifest.json \
  --change "Single-pass validated model selection in Go HTTP gateway" \
  --target-concurrency 2 \
  --min-rps-gain 5 \
  --max-regression 5 \
  --output reports/local/optimization-signal.json
```

The output is a **sanitized, descriptive JSON signal** with all three
concurrency levels 1, 2, 4; RPS, p95 latency and p95 TTFT percent
changes, quality parity and explicit downside flags. It never reproduces
prompts, model replies, absolute paths, JWTs, tokens or local labels.
A new JSON report is created exclusively; do not overwrite private raw
evidence or commit `reports/local/`.

Return code **0** is deliberately narrow: the source comparator first
requires at least **30 independent quality references** and **30 measured
requests/run**, complete provider-reported token usage, matching original
run metadata, no failed requests and no exact/normalized quality
regression. Then the selected target concurrency must show at least the
declared RPS gain and **none of the three concurrency levels** may have
more than the allowed RPS decrease, p95 latency increase or p95 TTFT
increase. Even this state is called
`descriptive_guardrails_met_not_causal`, **never "proven speedup"**.

Return code **2** means incomplete/ineligible evidence, insufficient
gain, a significant adverse tradeoff, or malformed/unsafe raw results.
When a candidate worsens one level, the complete delta matrix and
tradeoff flags remain in the JSON; a single cherry-picked concurrency
does not establish a successful optimization. Cases with baseline
throughput equal to zero or missing p95 fail closed.

## Important interpretation constraints

- The existing October 13 study changed an **Ollama server setting**;
  it was not a Metriqen code path optimization and cannot satisfy P3.
- A synthetic Go JSON parser microbenchmark is valuable
  algorithmic evidence but not a real token inference speedup.
- `descriptive_guardrails_met_not_causal` does NOT mean statistical
  significance, authenticated hardware execution or P3 release approval.
- Provider self-reported token counts are not independently calculated
  generated-token usage, project billing or costs.
- The rule does **not** check GPU memory, power, thermal throttling,
  identity-provider correctness, real model quality or metric uncertainty.
- Operator inspection of both raw directories, A/B/A/B drift, exact code
  change, fairness and negative results is still mandatory.
- The CLI does not download models, restart services or send inference.

Use [A/B/A/B preflight binding](phase-preflight-binding.md) and the
[portfolio release contract](../PORTFOLIO_V1_ACCEPTANCE.md). P3 remains
**unfinished until actual, operator-reviewed end-to-end serving evidence
and a precise code attribution exist**.
