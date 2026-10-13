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

4. With `--stream`, the benchmark now requests `"stream_options":{"include_usage":true}` by default (verified through the local Go gateway with Ollama 0.30.8). If a different OpenAI-compatible runtime rejects this optional field, rerun with `--no-stream-usage`; this intentionally leaves streaming token throughput `null`. The JSON report records `configuration.stream_usage_requested` so missing usage is interpretable. Non-stream requests never include `stream_options`.
5. Run a concurrency sweep: 1, 2, and 4 with the **same** dataset, model revision, runtime, prompt/output caps, generation settings, and warm-up. Record failures. Gateway local admission limit is four, so concurrency is deliberately capped at four.
6. Run test suite: python -m unittest discover -s tools/benchmark/tests -v.

Reports under reports/ must be reviewed for privacy and hardware claims before publication.

## Repeated concurrency experiments

For a repeatable Apple M2 Ollama sweep at concurrency 1, 2 and 4, use the [nine-run sweep CLI](repeated-sweep.md). It pins the supplied 64-character model digest, rotates run order, retains failures and refuses to overwrite previous evidence. Per-run summaries are descriptive measurements; they do not establish GPU optimization improvements.

## Measurement definitions

| Field | Definition |
| --- | --- |
| latency_ms p50/p95 | Client-observed completed request times for **successful** requests, nearest-rank percentile |
| ttft_ms | Streaming only: first SSE event with nonempty choices[].delta.content; role-only events ignored |
| requests_per_second | Successful measured requests divided by batch wall time, including client worker waiting |
| stream_usage_requested | Whether this benchmark sent the OpenAI-compatible `stream_options.include_usage` request field (streaming only) |
| completion_token_coverage | Successful requests with an integer usage.completion_tokens value |
| output_tokens_per_second | Sum of reported completion tokens / batch wall time, only if every success reports usage; otherwise null |
| failures/errors | Non-2xx, malformed SSE/JSON, in-band error events, incomplete `[DONE]` streams and network errors; HTTP 429 counts as failure |
| observations | Per-request index/status/timing/token usage/error; excludes prompt and generated text |

Warm-ups are sequential and excluded from measurements; failed warm-ups abort the benchmark. Nonstream TTFT is null. Runtime/hardware fields are self-reported and not independently verified.

The streaming benchmark parser accepts standard SSE event framing, including multiple `data:` lines and comments. It rejects in-band `error` events (even after content was emitted), malformed choices/deltas, missing `[DONE]`, and streamed responses above **1 MiB**. It reports only safe error categories, never error-body text. The Go gateway separately enforces an **8 MiB** streaming forwarding budget; the smaller benchmark cap is intentional for controlled experiments.

## Fair comparisons and limitations

- Pin exact model revision, quantization, runtime version, hardware, prompt/output caps, decoding settings, and concurrent traffic profile.
- Runner sets temperature 0, but actual generated token counts may still vary. Check output length and model quality before attributing improvement.
- Repeat runs to capture variance. A single run's p95 is not a confidence interval.
- Runner does **not** collect GPU VRAM, pricing, traces, verified tokenizer counts, quality evaluation, or intertoken latency.
- HTTP localhost only; no redirects or proxy, no production traffic.
- Socket timeouts are per I/O operation, not guaranteed end-to-end limits against slow trickling responses.

**Do not claim an optimization benefit without real-model/real-hardware measurements and comparable output quality.**

**Field verification:** On a local Apple M2 running Ollama 0.30.8, a manual Metriqen streaming test with `stream_options.include_usage=true` returned a final `choices: []` chunk with `prompt_tokens=32` and `completion_tokens=35`, then `[DONE]`. This verifies runtime compatibility, not a complete performance study. An earlier six-request user-provided baseline had 6/6 successes but no streaming usage; do not recompute or backfill its missing token counts from a separate request. Repeat that benchmark with this version to collect token throughput legitimately.

## Validated comparison (new)

For paired local performance runs and an independently checked quality reference, use the [controlled comparison protocol](controlled-comparison.md). The repeat sweep now includes median/min/max of per-run p95 latency and TTFT as well as per-run p50. These summaries are descriptive; neither CI fixture timings nor unpinned model tags constitute performance evidence.
