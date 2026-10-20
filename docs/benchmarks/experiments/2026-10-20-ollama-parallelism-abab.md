# 2026-10-20 local Ollama parallelism A/B/A/B — descriptive replication

**Classification:** `descriptive_replication_only` (operator-sourced, not causal). This is a **configuration** experiment comparing Ollama `OLLAMA_NUM_PARALLEL=1` (A1/A2) with `2` (B1/B2), **not** a Metriqen-written inference optimization or a P3 speedup.

The repository's `tools.benchmark.four_phase_review` returned `P2 four-phase private evidence consistency: PASS` on October 20, 2026. That verifies chronology and internal consistency of owner-provided preflights/raw evidence, **not** hardware identity, live runtime-slot authenticity, uninterrupted AC power or causal performance improvement. The sanitized figures below were transcribed from the operator's resulting `abab-review.md`; the original inputs and private generated reports stay Git-ignored under `reports/local/`. A human P2 portfolio acceptance review remains open.

## Fixed experiment identity and sample design

| Field | Recorded observation |
| --- | --- |
| Host | Operator-reported Apple M2, Metal, macOS |
| Ollama server | 0.40.6, locally operated |
| Model | `llama3.2:1b`, Q8_0 |
| Model digest (SHA-256) | `baf6a787fdffd633537aa2eb51cfd54cb93ff08e28040095462bb63daf552878` |
| Performance dataset SHA-256 | `6c209cd4008d0cd0d34356931934a8881361c121b52a4069916100342ef943d3` |
| Quality dataset SHA-256 | `5bda762c02bb5bc419ed7a2657c2ab17ec90bdd4239d9feeffe1d289de0f8ffc` |
| Phase sequence | A1 = parallelism 1; B1 = 2; A2 = 1; B2 = 2 |
| Per phase | 3 repetitions at offered concurrency 1, 2 and 4; 36 measured requests per run; 36 separate reference-quality completions |
| Across all phases | 36 timed runs; **1,296/1,296 successful measured requests**; 144 quality completions |
| Aggregation | Median of the **three run-level summaries**, not a pooled request percentile |

The workload and narrow answer-reference dataset are versioned in [performance workload](../workload.v1.jsonl) and [quality workload](../../nlp-eval/workload.v1.jsonl). Decoding parameters and collection procedure are specified in the [phase runner protocol](../phase-runner.md); the offline reviewer checks pinned dataset fingerprints and matched evidence.

## Observed performance (run-median summaries)

Higher requests/s is better; lower p95 latency and p95 time-to-first-token (TTFT) are better. Do **not** interpret ratios of these small samples as statistical significance.

| Offered C | Measure | A1 (slots 1) | B1 (slots 2) | A2 (slots 1) | B2 (slots 2) |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | Successful requests/s | 0.276 | 0.364 | 0.372 | 0.288 |
| 1 | Output tokens/s | 29.033 | 38.359 | 39.123 | 30.369 |
| 1 | Successful latency p95 (ms) | 4827.802 | 3428.776 | 3610.325 | 4375.480 |
| 1 | TTFT p95 (ms) | 212.612 | 225.042 | 320.051 | 819.445 |
| 2 | Successful requests/s | 0.302 | 0.724 | 0.402 | 0.371 |
| 2 | Output tokens/s | 31.771 | 76.388 | 42.360 | 39.075 |
| 2 | Successful latency p95 (ms) | 8673.188 | 3883.006 | 6709.839 | 8134.189 |
| 2 | TTFT p95 (ms) | 4678.616 | 318.581 | 3514.913 | 3810.405 |
| 4 | Successful requests/s | 0.271 | 0.651 | 0.375 | 0.432 |
| 4 | Output tokens/s | 28.474 | 68.657 | 39.459 | 45.615 |
| 4 | Successful latency p95 (ms) | 16973.611 | 7630.157 | 11834.951 | 11533.174 |
| 4 | TTFT p95 (ms) | 15341.740 | 3826.127 | 9344.574 | 7494.735 |

### Replication and same-setting drift

| Offered C | A1 → B1 throughput | A2 → B2 throughput | A1 → A2 drift (same slots 1) | B1 → B2 drift (same slots 2) |
| ---: | ---: | ---: | ---: | ---: |
| 1 | +32.12% | **−22.36%** | +34.73% | −20.83% |
| 2 | **+139.79%** | **−7.73%** | +33.33% | **−48.69%** |
| 4 | +140.52% | +15.34% | +38.56% | −33.55% |

- **The candidate effect is not stable.** At C=1 and C=2 the second A→B throughput contrast reversed direction. At C=4 both contrasts were positive but materially smaller in the second pair.
- **Drift is large.** For example B1→B2 at C=2 lost 48.69% requests/s without changing the declared parallelism setting. Treat the early +139.79% change as *observed in B1*, not an independently reproduced gain.
- **Latency is not a consistent win.** At C=2, p95 latency changed −55.23% in A1→B1 but **+21.23%** in A2→B2; C=2 TTFT p95 changed −93.19% versus **+8.41%**.
- **The reported quality reference checks are identical:** exact string match **86.11%**, normalized exact match **91.67%**, for all four 36-example phases. This is only a narrow matching metric, not a semantic, safety or industrial generalization guarantee.

### Measurement chronology (UTC)

| Phase | First run | Last run |
| --- | --- | --- |
| A1 | 2026-10-20 01:30:49 | 01:49:42 |
| B1 | 2026-10-20 02:09:57 | 02:20:14 |
| A2 | 2026-10-20 04:08:06 | 04:21:48 |
| B2 | 2026-10-20 07:13:58 | 07:29:19 |

## Observations, unresolved controls, and interpretation

- The operator showed Ollama model-load logs with `n_seq_max=1`, `n_slots=1`, and a runner command with `-np 1` before A2 collection. For B2, logs showed `n_seq_max=2`, `n_slots=2`, and a runner with `-np 2` before and after collection. These are **operator observations**, not remotely authenticated slots.
- **B1 limitation:** the observed runner with `-np 2` was recorded *later* than the B1 run rather than as an independently preserved benchmark-time slot observation.
- **A2 limitation:** an AC-powered preflight passed, but a later battery reading means **continuous AC power during collection is not established**. The A2 evidence manifest includes this caveat.
- **B2 limitation:** an initial preflight was blocked on battery. The operator reconnected AC, obtained a fresh passing AC-powered preflight, and showed AC power after collection; uninterrupted power/thermals were not independently monitored.
- Temperature, caching, host contention, real GPU/Metal utilization and per-phase thermal drift were **not independently controlled**. Three run-median repetitions per concurrency do not establish confidence intervals for the true p95 distribution. Provider-reported output-token counts are not independently attested usage.
- The private reviewer returned a **consistency PASS**, not a causal-speedup verdict. Original private raw reports, prompt/model text and local filesystem paths are **not published** here.

**Conclusion:** In this bounded workload, some observations with Ollama parallelism 2 were faster and some were slower. The strong A1→B1 throughput/TTFT results were not reliably replicated by A2→B2, and there was pronounced same-setting drift. **No general speedup, P2 human sign-off, or P3 Metriqen-controlled improvement is claimed.**

## Reproduction and next engineering work

1. Follow [guarded phase collection/finalization](../phase-runner.md), with AC, exact local Ollama process settings and separate contemporaneous n_slots/thermal logs; collect prospective A1/B1/A2/B2 evidence rather than modifying these original reports.
2. Run the [offline four-phase reviewer](../abab-replication.md) and inspect *both* candidate contrasts and *both* same-setting drift contrasts, including quality and repeat ranges.
3. For **P3**, independently compare Metriqen Gateway `METRIQEN_QUEUE_DEPTH=0` vs `2` under [the FIFO overload protocol](../admission-overload.md). Keep Ollama slots **fixed** on both conditions, and count all offered requests, HTTP 429s, goodput, p95 latency, TTFT and regressions.
4. Human-review evidence, reviewer demo and P1–P6 release criteria in the [release contract](../../PORTFOLIO_V1_ACCEPTANCE.md). No CI run can attest private physical-Mac execution.

This report is new October 20 evidence; it must **not** overwrite or be conflated with the separate [October 13 descriptive study](2026-10-13-ollama-parallelism.md).
