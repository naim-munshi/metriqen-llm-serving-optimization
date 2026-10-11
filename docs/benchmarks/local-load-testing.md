# Reproducible local inference benchmark

**Status:** An initial measurement harness, not a GPU optimization result. The local Go gateway is unauthenticated and must not be exposed to a network. Tests use fake HTTP inference responses. Do not publish synthetic test numbers as model performance.

## Purpose

Measure a stable workload against a **localhost-only** OpenAI-compatible chat endpoint. The runner uses the Python standard library, including ThreadPoolExecutor, with no package installation beyond Python 3.12+.

This is an observational client-side load test, not a claim of server GPU utilization, model accuracy, tokenization correctness, or causal improvement.

## Dataset contract

Provide UTF-8 JSONL with exactly one messages field on each line. Every message has a non-empty role string and a content field. Do not benchmark production or sensitive prompts unless explicitly approved. Example:

~~~json
{"messages":[{"role":"user","content":"Briefly describe continuous batching."}]}
{"messages":[{"role":"user","content":"Explain the purpose of a KV cache in one sentence."}]}
~~~

The runner hashes dataset bytes with SHA-256 but does **not** write prompts or completions into reports. The hash identifies dataset contents, not correctness. Use a fixed dataset and pin the actual model artifact/revision for comparable runs.

## Local execution

1. Start local Ollama and load a model. Verify direct inference before using the gateway.
2. Start Metriqen's Go gateway:

~~~sh
cd services/inference-gateway
METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 go run ./cmd/server
~~~

3. From the repository root, execute:

~~~sh
python -m tools.benchmark.runner \
  --model llama3.2:1b \
  --model-revision llama3.2:1b \
  --runtime 'Ollama: record actual version' \
  --hardware 'Apple Silicon M1: record RAM and environment' \
  --dataset docs/benchmarks/workload.example.jsonl \
  --stream --warmup 3 --requests 30 --concurrency 2 \
  --max-tokens 64 --output reports/local/baseline-c2.json
~~~

4. Run a concurrency sweep: 1, 2, and 4 with the **same** dataset, model revision, runtime, prompt/output caps, generation settings, and warm-up. Record failures. Gateway local admission limit is four, so concurrency is deliberately capped at four.
5. Run test suite: python -m unittest discover -s tools/benchmark/tests -v.

Reports under reports/ must be reviewed for privacy and hardware claims before publication.

## Measurement definitions

| Field | Definition |
| --- | --- |
| latency_ms p50/p95 | Client-observed completed request times for **successful** requests, nearest-rank percentile |
| ttft_ms | Streaming only: first SSE event with nonempty choices[].delta.content; role-only events ignored |
| requests_per_second | Successful measured requests divided by batch wall time, including client worker waiting |
| completion_token_coverage | Successful requests with an integer usage.completion_tokens value |
| output_tokens_per_second | Sum of reported completion tokens / batch wall time, only if every success reports usage; otherwise null |
| failures/errors | Non-2xx, invalid output, network errors and missing stream terminators; HTTP 429 counts as failure |
| observations | Per-request index/status/timing/token usage/error; excludes prompt and generated text |

Warm-ups are sequential and excluded from measurements; failed warm-ups abort the benchmark. Nonstream TTFT is null. Runtime/hardware fields are self-reported and not independently verified.

## Fair comparisons and limitations

- Pin exact model revision, quantization, runtime version, hardware, prompt/output caps, decoding settings, and concurrent traffic profile.
- Runner sets temperature 0, but actual generated token counts may still vary. Check output length and model quality before attributing improvement.
- Repeat runs to capture variance. A single run's p95 is not a confidence interval.
- Runner does **not** collect GPU VRAM, pricing, traces, verified tokenizer counts, quality evaluation, or intertoken latency.
- HTTP localhost only; no redirects or proxy, no production traffic.
- Socket timeouts are per I/O operation, not guaranteed end-to-end limits against slow trickling responses.

**Do not claim an optimization benefit without real-model/real-hardware measurements and comparable output quality.**
