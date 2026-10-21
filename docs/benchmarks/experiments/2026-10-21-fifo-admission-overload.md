# 2026-10-21 Metriqen FIFO admission policy — local A/B tradeoffs

**Classification:** `descriptive_tradeoffs_not_causal_or_attested`. **Gate:** P3 evidence collected and offline-consistency reviewed; **human P3 acceptance pending**. This compares an actual **Metriqen-controlled Go gateway admission policy**, not Ollama `OLLAMA_NUM_PARALLEL`, llama.cpp kernels, or an inference-speed optimization.

## Observed study (operator-sourced)

| Field | Baseline A | Candidate B |
| --- | --- | --- |
| Go gateway `METRIQEN_QUEUE_DEPTH` | `0` (immediate 429 above four active) | `2` (bounded FIFO, maximum five-second wait) |
| Gateway active request cap | `4` | `4` |
| Ollama configured parallelism | `2` | `2` |
| Model | `llama3.2:1b`, Q8_0 | Same |
| Pinned Ollama model SHA-256 | `baf6a787fdffd633537aa2eb51cfd54cb93ff08e28040095462bb63daf552878` | Same |
| Runtime / hardware (operator reported) | Ollama 0.40.6, Apple M2 Metal | Same |
| Benchmark design | Three repeats at offered C=4/6/8/12, 36 measured requests each | Same |
| Collected | 12 timed runs, 432 measured requests + 36 distinct reference answers | Same |
| Private phase | `admission-A-20261021-134108` | `admission-B-20261021-135909` |

The operator reported `P3 admission phase collection: PASS` for both phases and `P3 FIFO overload private tradeoff review: PASS (descriptive only)` after checking the two private `overload` directories. Those tools validate report structure/chronology/counters and matched reported configuration; they **do not** authenticate the hardware or establish causality. The exact A/B raw reports and quality files remain under Git-ignored `reports/local/` and are **not** published.

Before collection, the operator observed a loaded, digest-matched Q8_0 model, llama.cpp `-np 2` and model-load logs showing `n_seq_max=2`, `n_slots=2`. A and B phase preflight and postflight each recorded AC power and configured parallelism 2. Continuous AC, actual runner state throughout both phases, thermal conditions, GPU execution and background contention were not independently monitored. An **afterward** battery-power observation does not establish that either earlier phase ran on battery, nor does it certify uninterrupted AC.

## Outcome matrix

Each concurrency has **108 offered measured requests** (3 × 36). RPS and successful-only latency/TTFT are the median of three separate run-level summaries, not a pooled percentile.

| Offered C | A successes | B successes | A HTTP 429 | B HTTP 429 | A goodput (RPS) | B goodput (RPS) | B goodput delta |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 108 | 108 | 0 | 0 | 0.6786 | 0.5649 | −16.76% |
| 6 | 12 | 108 | 96 | 0 | 0.8619 | 0.7656 | −11.17% |
| 8 | 12 | 18 | 96 | 90 | 0.8838 | 0.6043 | −31.62% |
| 12 | 12 | 18 | 96 | 90 | 0.8111 | 0.5903 | −27.22% |
| **All C** | **144/432 (33.33%)** | **252/432 (58.33%)** | **288** | **180** | — | — | **No level improved** |

Across all offered requests, Candidate B completed 108 more and returned 108 fewer client-observed HTTP 429s (25 percentage points greater overall success rate; 37.5% fewer 429s). These counts do not establish higher throughput, reduced execution cost or fewer upstream failures. Raw gateway admission-counter deltas must distinguish Go rejections from any upstream HTTP 429.

| Offered C | A successful-only p95 latency | B successful-only p95 latency | A successful-only p95 TTFT | B successful-only p95 TTFT |
| ---: | ---: | ---: | ---: | ---: |
| 4 | 12,862 ms | 8,987 ms | 5,187 ms | 4,709 ms |
| 6 | 4,640 ms | 10,934 ms | 2,531 ms | 7,136 ms |
| 8 | 4,524 ms | 9,922 ms | 2,446 ms | 6,142 ms |
| 12 | 4,926 ms | 10,154 ms | 2,558 ms | 6,248 ms |

At C=6, B's successful-only p95 latency increased approximately **135.6%** and p95 TTFT approximately **181.9%**; both worsened at C=8 and C=12. **Selection bias matters:** A's p95 above C=4 covers only 12 successes per 108 offered, while B's covers a different success population. Present success, 429 and latency together; neither successful-only p95 nor a high acceptance fraction alone demonstrates a better experience.

**Sanity check at C=4:** Both variants completed all requests and neither should need its pending queue when the four active gateway slots suffice. The divergent C=4 goodput and latency therefore caution strongly against assigning performance changes exclusively to queue depth. Sequential phases, warmup/cache/thermal variability, client request scheduling, and model generation variability could confound measured contrasts. Thread-pool concurrency is not a fixed arrival-rate experiment.

## Reference-answer quality (separate 36-request run per phase)

| Narrow metric | Baseline A | Candidate B |
| --- | ---: | ---: |
| Completed quality requests | 36/36 | 36/36 |
| Exact reference string match | 31/36 (86.11%) | 31/36 (86.11%) |
| Normalized exact string match | 33/36 (91.67%) | 33/36 (91.67%) |

Matched narrow scores are **not** proof of identical individual answers, semantic/factual quality, safety, or zero degradation on a representative dataset. A/B references and prediction text remain private.

## Engineering interpretation and acceptance boundary

1. The existing bounded Go FIFO admission policy **increased observed completion/acceptance** under this workload, particularly at C=6; this is attributable to the **policy under test** only in the limited sense that Go queue depth is the recorded changed setting, not an authenticated causal effect.
2. It **did not improve successful-request goodput** at any tested concurrency, and it substantially worsened successful-only latency/TTFT when overloaded. Do **not** call this a serving speedup or enable the queue by default on this evidence.
3. The reviewer returned `portfolio_p3_complete: false`, `causal_speedup_verified: false`, `model_quality_verified: false` by design. P3 formal human acceptance remains **open** pending inspection of raw gateway-counter classifications, queue-timeout/wait histograms, report provenance, matched config logs, latency tradeoffs, and quality files.
4. A follow-on targeted engineering change is justified **only** if a stated user-facing objective (e.g. reduce 429s within a p95/TTFT bound) can be met without hiding failures. An A/B/A or interleaved repeat under monitored stable power/thermals would better characterize drift. Preserve the first A/B reports unchanged.
5. Publish only these sanitized operator-transcribed aggregates **after owner review**. CI can check documentation/source contracts but cannot inspect the original Mac evidence, attest a Metal/GPU result, or mark P3/P1–P6 complete.

## Reproduce and independently check

- [FIFO overload experiment protocol](../admission-overload.md) — bounded local collector `python3 -m tools.benchmark.admission_phase` with `--phase A` / `--phase B`, fixed Ollama parallelism and private output.
- [P3 offline strict tradeoff reviewer](../../../tools/benchmark/admission_tradeoff.py) — point `--baseline-dir` and `--candidate-dir` to the corresponding **private `overload/` subdirectories**; this checks every raw request outcome but does not validate reference-answer parity.
- [Portfolio v1 acceptance contract](../../PORTFOLIO_V1_ACCEPTANCE.md) and [release tracker #81](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81).

**Status:** Draft aggregate awaiting source-owner privacy and scientific review, not a signed-off performance claim.
