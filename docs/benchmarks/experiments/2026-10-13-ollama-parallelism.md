# First observed local experiment: Ollama parallelism 1 versus 2

**Experiment date:** 2026-10-13 (Japan)  
**Scope:** developer-operated local Apple M2 model serving via the Metriqen Go gateway  
**Evidence level:** *operator-attested, consistency-checked private local reports; public aggregates manually transcribed from the operator's terminal.*  
**Comparator verdict:** `descriptive_not_causal` — **not a verified causal speedup or production benchmark**.

> Headline observation: at offered concurrency 2, switching from Ollama `OLLAMA_NUM_PARALLEL=1` to `2` was associated with a **+34.235%** change in successful requests/s and a **−88.991%** change in median per-run TTFT p50; exact/normalized match was unchanged on 36 simple questions. At concurrency 1, TTFT p50 **worsened by +27.025%**. All results are descriptive.

## Research question and hypothesis

Does increasing local Ollama inference parallel slots from **one to two** improve latency and throughput when clients submit **1, 2 or 4** simultaneous requests, without reducing deterministic reference-answer match on the same model?

The system previously exposed a Go loopback-only, OpenAI-compatible streaming proxy with a maximum of four in-flight requests. The experiment changes the upstream **Ollama scheduling parallelism**, **not** a custom batching algorithm within Metriqen. The actual backend runs Ollama / llama.cpp with Metal acceleration. No vLLM, NVIDIA GPU, public production serving, cost accounting or model training was involved.

## Model, hardware, and controlled inputs

| Parameter | Value |
| --- | --- |
| Model | `llama3.2:1b` (Llama 3.2 1B Instruct) |
| Operator-recorded model digest | `baf6a787fdffd633537aa2eb51cfd54cb93ff08e28040095462bb63daf552878` |
| Quantization | Q8_0 (GGUF V3) |
| Runtime | Ollama 0.30.8; Go gateway loopback `127.0.0.1:8081`; Ollama loopback `127.0.0.1:11434` |
| Hardware | Apple M2 MacBook Air, 8 GiB unified memory, Metal |
| Changed setting | `OLLAMA_NUM_PARALLEL=1` to `2` |
| Local slot evidence | Baseline: `n_seq_max=1`, `n_slots=1`; candidate: `n_seq_max=2`, `n_slots=2` |
| Per configuration | 3 repeated runs each at concurrency 1, 2, 4 = 9 runs |
| Per run | 36 measured requests + 2 warmups; streaming, `temperature=0`, `max_tokens=128`, 120-second client timeout |
| Performance inputs | [36-prompt synthetic v1 workload](../workload.v1.jsonl); short/medium/long **character** buckets, not verified token buckets |
| Quality inputs | [36-reference synthetic v1 workload](../../nlp-eval/workload.v1.jsonl); exact and normalized string match |
| Measured requests | 324 per setting, 648 total; **648 successful / 0 failed** |

The operator ran each setting sequentially on the same laptop, and the comparator checked the dataset fingerprints, model digest, hardware label, runtime, common runner settings, raw request outcomes, output-token coverage and quality metadata for consistency. The benchmark used rotating concurrency order across repetitions. Local logs verified that model layers were offloaded through Metal and that the candidate used two slots. This is **not an independently repeated or cryptographically attested comparison**.

## Results

Each performance value is the **median across three per-run summaries** for the stated concurrency, *not* a percentile pooled across 108 requests. Changes were computed as `(candidate / baseline − 1) × 100%`. A negative percentage for latency or TTFT is directionally favorable.

| Concurrency | Metric | Baseline P=1 | Candidate P=2 | Change |
| --- | --- | ---: | ---: | ---: |
| 1 | Successful requests/s | 0.1566 | 0.1505 | -3.895% |
| 1 | Output tokens/s | 17.1170 | 16.4497 | -3.898% |
| 1 | Latency p95 (s) | 8.966 | 8.801 | -1.839% |
| 1 | TTFT p50 (s) | 0.393 | 0.500 | +27.025% |
| 2 | Successful requests/s | 0.1811 | 0.2431 | +34.235% |
| 2 | Output tokens/s | 19.7925 | 26.5674 | +34.230% |
| 2 | Latency p95 (s) | 13.561 | 11.164 | -17.679% |
| 2 | TTFT p50 (s) | 5.940 | 0.654 | -88.991% |
| 4 | Successful requests/s | 0.2101 | 0.2374 | +12.994% |
| 4 | Output tokens/s | 22.9630 | 25.9531 | +13.021% |
| 4 | Latency p95 (s) | 21.874 | 20.788 | -4.964% |
| 4 | TTFT p50 (s) | 14.251 | 8.513 | -40.263% |

**Client concurrency 2 showed the largest improvements:** measured requests/s rose **34.235%**, output tokens/s rose **34.230%**, p95 latency fell **17.679%**, and TTFT p50 fell **88.991%**.

**Trade-offs are part of the result.** At concurrency 1, requests/s fell 3.895%, token throughput fell 3.898%, and TTFT p50 rose 27.025% despite a 1.839% reduction in p95 latency. At concurrency 4, TTFT p50 still exceeded 8 seconds with the two-slot candidate. The experiment does **not** demonstrate consistently better user experience across all workload conditions.

### Separate deterministic answer-quality check

| Metric | P=1 | P=2 |
| --- | ---: | ---: |
| Generation success | 36/36 | 36/36 |
| Exact match | 31/36 (86.11%) | 31/36 (86.11%) |
| Normalized exact match | 33/36 (91.67%) | 33/36 (91.67%) |

The dataset only tests a few capitals, arithmetic operations and computing acronym expansions. Two fewer exact than normalized matches illustrate format sensitivity. This is **not semantic correctness, hallucination, safety or broad quality parity**. These are nonstreaming evaluations, so their client latencies must **not** be conflated with streaming TTFT.

## Validation, provenance and reproduction

The developer-operated CLI produced two separate private nine-run sweep directories, two 36-question quality reports, and two human-attested evidence manifests. Both manifests were checked with `python3 -m tools.benchmark.evidence_gate ... --verify-reports`; the separate `tools.benchmark.compare` CLI completed with `comparison_status: descriptive_not_causal` and reported the percentages above. Neither raw report nor the complete comparison JSON is included in this GitHub repository. CI checks the arithmetic of the [published aggregate transcription](2026-10-13-ollama-parallelism.json) but **cannot validate the original run contents or prove GPU execution**.

**Local directory names used by the operator:**

- `reports/local/v1-baseline-20261013-104946` for `OLLAMA_NUM_PARALLEL=1`;
- `reports/local/v1-parallel2-20261013-120425` for `OLLAMA_NUM_PARALLEL=2`;
- `reports/local/v1-parallel2-comparison-20261013-132329.json` for the local strict comparison.

All those files are intentionally ignored by Git. Only the aggregate transcription is shared. For full rerun instructions and authenticity caveats see [controlled comparison](../controlled-comparison.md), [repeat sweep](../repeated-sweep.md) and [evidence bundle](../real-model-evidence-bundle.md).

On a local development machine with the **same pinned model digest** and versioned datasets, the operator should set `OLLAMA_NUM_PARALLEL` before running the Ollama server, verify active slots in Ollama's log, and run the same 9-run sweep in a **new directory**, then separately evaluate reference quality. Repeat for the second setting; run the evidence gate before calling the strict comparison CLI. The runtime/hardware labels and machine conditions must be recorded from the actual environment and cannot be copied blindly.

## Threats to validity and next experiments

- **One machine, one sequential before/after session**: thermal throttling, unified-memory pressure, OS scheduler activity, background processes and caching may differ across phases. Model-load `n_ctx` and KV cache allocation grew with the slot count (128 MiB to 256 MiB), which is part of the configuration effect; the reported change is not solely isolated from memory pressure.
- **Small synthetic workloads**: 36 prompts reused for every run; prompt cache can influence timing and near-identical long templates can exaggerate cache reuse. Per-concurrency p95 is estimated from only 36 requests per run and is sensitive to outliers.
- **No statistical uncertainty estimate**: three run-level medians per configuration are not confidence intervals. The comparator validates comparability at the configured metadata level, **not true causal attribution**.
- **No per-request public data or quality-response review**: the repo has sanitized aggregate values, not original artifacts; no independent auditor can recalculate every result from GitHub alone.
- **No claim about broader hardware/runtime configurations**: Q8_0 Metal measurements do not generalize to other quantization, vLLM continuous batching, GPU VRAM pressure, production loads or cost.

**Recommended follow-up:** run the [documented A/B/A (1 → 2 → 1) reversion protocol](../aba-reversion-protocol.md) with cooling/background-load notes and new run directories; then assess whether the original result persists under controlled conditions. Only consider parallelism 4 or alternative queue scheduling after collecting additional latency, throughput and quality evidence. No claims of SLA attainment or statistical significance are made here.

## Recheck the published figures against private evidence (offline)

After `git pull --ff-only origin main` on the same Mac where the private
`reports/local/` directories still exist, run this **read-only** audit:

~~~sh
python3 -m tools.benchmark.audit_public_case_study \
  --published docs/benchmarks/experiments/2026-10-13-ollama-parallelism.json \
  --baseline reports/local/v1-baseline-20261013-104946/evidence-manifest.json \
  --candidate reports/local/v1-parallel2-20261013-120425/evidence-manifest.json
~~~

The CLI checks **all 18 local raw run reports**, both human-attested manifests,
the per-run aggregate calculations, quality scores, actual v1 dataset SHA-256
fingerprints and every published percentage. It refuses discrepancies and does
not upload or modify any local report. Ollama/Gateway **do not need to run** for
this offline check.

This gives a reproducible consistency check that the public transcription was
copied correctly **if the private files are authentic**, not remote
verification by GitHub CI. Active Ollama parallelism (1 versus 2), actual
hardware, thermal state and causal attribution still require independent
operator observation. Never commit `reports/local/` or publish prompt text.
