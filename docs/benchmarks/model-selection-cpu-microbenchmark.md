# Synthetic gateway model-selection microbenchmark

**Scope:** Go gateway request-side JSON model selection only. This is an
optimization of repeated parsing, NOT LLM serving throughput, GPU batching,
KV cache, token generation, TTFT, p95 end-to-end latency, inference quality,
or hardware cost. No model inference occurs in the benchmark.

## Optimization contract

Earlier in the live `/v1/chat/completions` handler, `validateChatRequest`
parsed and sanitized the JSON request and then strict
`X-Metriqen-Deployment` authorization **decoded the sanitized whole JSON
again**, while an enabled `METRIQEN_ALLOWED_MODELS` allowlist performed
**another whole-JSON decode** of exactly the same request to extract its
`model` field.

The optimized handler calls `validateChatRequestWithModel` and reuses that
already validated `model` string for *both* checks, retaining the existing
sanitized output bytes, chosen model identity, validation error semantics
and authorization order. The compatibility `validateChatRequest` function
remains. No cache of requests, tokens, inference results, model digests,
credentials or prompts was added. No cross-project request data is shared.

For security, both selection checks still occur before scheduler admission,
and strict model matching still depends on a fresh trusted Control API
decision and a per-request Ollama `/api/ps` digest probe. Synthetic
adversarial tests check duplicate JSON keys, Unicode-escaped property keys,
invalid model values, changed model between successive calls and unchanged
forwarded JSON. The existing Go race tests continue to check real gateway
behavior with simulated upstream services.

## Reproduce on a fixed host

From repository root, using a pinned Go toolchain:

```sh
cd services/inference-gateway
go version
GOMAXPROCS=1 go test -run '^$' \
  -bench '^BenchmarkModelSelection$' \
  -benchtime=250ms -count=5 -benchmem -cpu 1 ./internal/httpapi
```

The command measures two paired procedures in one Go test binary:

- `baseline_double_decode`: full validation, then two full JSON parses
  for the strict deployment model and local allowlist.
- `optimized_single_pass`: full validation, then direct reuse of the
  validated string for the two equivalent comparisons.

Both procedures consume identical **synthetic** JSON requests, model tag,
allowlist, Go runtime and CPU scheduling. Compare five `ns/op` runs and
`B/op`, `allocs/op`. A ratio of baseline median to optimized median can
quantify the isolated CPU-path speedup on that machine, **not whole request
RPS or end-to-end inference speedup**. Small results and noisy hosted CI
runners should not be overinterpreted; hence CI verifies that benchmarks run
but makes no numeric performance threshold assertion. Keep this benchmark
separate from the `tools.benchmark` real-Ollama A/B or A/B/A/B evidence
protocols and do not cite it as GPU performance.

GitHub Actions job: `gateway-model-selection-microbench`.
The output is visible in workflow job logs and should always be interpreted
with actual host, Go version and collection date. The job uses
`GOMAXPROCS=1` for comparable model-selection CPU microbench loops, not
an assertion that real inference uses a single worker.
