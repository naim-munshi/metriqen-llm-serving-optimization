# Paired Go request-model selection microbenchmark — 2026-10-18

**Result scope: gateway CPU-side JSON model-selection only**, on a synthetic
request using GitHub Actions hosted `ubuntu-latest` CI. This is not a live
Ollama, Mac M2, GPU, inference, network round-trip or end-to-end latency test.

## Source of observations

- Source: [PR #74 CI run 39046828682](https://github.com/naim-munshi/metriqen-llm-serving-optimization/actions/runs/39046828682), job `gateway-model-selection-microbench`.
- Runtime observed in logs: `go version go1.27.2 linux/amd64`.
- Paired command: `GOMAXPROCS=1 go test -run '^$' -bench '^BenchmarkModelSelection$' -benchtime=250ms -count=5 -benchmem -cpu 1 ./internal/httpapi`.
- Both paths use the same synthetic 2.7 KiB chat request, model, allowlist and JSON validation rules. Baseline re-decodes the sanitized entire request for deployment routing and the allowlist; optimized reuses the same model value already decoded during sanitization.
- Five sequential observations per variant on a hosted CI runner. No randomized A/B/A/B order, independent host replication, real traffic, warm thermal state, confidence interval, or GPU model was used.

| Observation | Baseline double decode (ns/op) | Optimized single pass (ns/op) |
| --- | ---: | ---: |
| 1 | 25,176 | 16,276 |
| 2 | 25,911 | 16,036 |
| 3 | 25,368 | 16,258 |
| 4 | 25,350 | 16,301 |
| 5 | 24,994 | 15,804 |
| **Median** | **25,350** | **16,258** |

On this runner, the paired median **model-selection CPU processing time**
decreased by approximately **35.9%**, while allocations changed from
**65 allocs/op to 43 allocs/op** and measured bytes per operation from
approximately **19,755 B/op to 13,370 B/op** (about **32.3% less**).
Because synthetic input and parsing operations dominate this microbenchmark,
those percentages cannot be extrapolated to actual LLM generation speeds,
throughput, time-to-first-token, p95 latency, memory of model weights,
GPU utilization or serving cost. Even these microbenchmark measurements may
vary across runs on a shared CI host.

## Security and correctness gates

The associated 11-job pull request CI verifies Go race tests, adversarial
model canonicalization (duplicate keys, Unicode-escaped keys and successive
different requests), the local inference E2E, Python control API, actual
ephemeral PostgreSQL and Redis CI checks. The optimized path preserves the
full JSON parse, validation bounds, strict server-authorized model matching,
per-request loaded-digest preflight and request-scoped isolation. No
authorization, caching or runtime security bypass is introduced.

**Next external validation:** use the existing real-model A/B/A/B workload
methodology on an operator-verified Mac/Ollama environment. Pair model digest,
hardware, concurrency, prompts, output token cap and actual Ollama slots.
Collect real throughput, TTFT, p95 latency, quality and energy evidence before
making any actual LLM serving performance claim.
