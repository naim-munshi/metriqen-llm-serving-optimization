# P3 diagnosis — native Ollama vs Go gateway (operator-only)

**Purpose:** isolate the **client-observed inference-path overhead** of
routing requests through Metriqen's Go gateway rather than sending the same
OpenAI-compatible call directly to local Ollama. This measures *total
path differences under two admission stacks*, not a new optimization.
This is a **diagnostic for what to optimize next**, not an experimental
claim that Metriqen's serving code improved throughput.

## Preconditions

- On the real Mac, already running Ollama on `127.0.0.1:11434` and
  Metriqen Go gateway on `127.0.0.1:8081`.
- Both endpoints point at the **same Ollama runtime and loaded pinned
  model**. The script can verify the installed `/api/tags` model digest
  and Go upstream reachability but **cannot** cryptographically attest
  the gateway's configured target or active Ollama `n_slots` at benchmark
  time. Operator must inspect real process args and runtime logs.
- AC power with steady thermal conditions, unchanged queue-depth, no
  other users/bursts, constant exact model digest/quantization, unchanged
  workload/decoding and same network loopback path.
- The default local developer gateway is not authenticated. If guarded
  bearer/JWT mode is active, **do not bypass it** for this comparison; it
  must be separately approved and instrumented.
- Local model usage and license must permit the evaluation.

## Dry-run first (zero inference requests)

```bash
cd ~/metriqen-llm-serving-optimization
source .venv/bin/activate
python3 -m tools.benchmark.gateway_overhead
```

It prints the proposed **ABBA** order: per concurrency 1, 2, 4,
`native → gateway → gateway → native`. This is 12 distinct phases with
12 measured streaming requests per phase and one warmup each by default,
**144 measured + 12 warmup** requests. It does not turn on services,
change Ollama settings, allocate GPU or create files in plan-only mode.

## Operator's deliberate real execution

First determine the **actual** lowercase 64-hex model digest from
`curl -fsS http://127.0.0.1:11434/api/tags` and confirm the correct
`llama3.2:1b` tag and active Ollama process. Do not invent a digest.

Only after reviewing the intended runtime/process/queue/AC/thermal
conditions, run:

```bash
python3 -m tools.benchmark.gateway_overhead \
  --execute \
  --model llama3.2:1b \
  --expected-digest ACTUAL_64_CHARACTER_LOWERCASE_DIGEST \
  --requests 12 \
  --max-tokens 64 \
  --output-dir reports/local/gateway-overhead-real-YYYYMMDD-HHMMSS
```

Use an actual new output directory name; do not reuse an existing
report path. Report files are private `0600` under ignored
`reports/local/`. On failure, any partial files remain private;
no aggregate is written, and the command returns nonzero.
For each phase it records failures/HTTP classifications as well as
success-only p95 latency, first-content TTFT and successful RPS.
These must be interpreted **together**; don't filter failures.

```text
reports/local/gateway-overhead-real-.../
  c1-phase1-native.json ... c4-phase4-native.json
  summary.json
```

## Interpretation and follow-up

- Look for consistent sign and magnitude of native vs gateway RPS,
  p95 TTFT and p95 latency within each ABBA pair, **plus every
  failure/HTTP 429 difference**, after independently checking AC,
  n_slots, model identity and thermals. Do not report a confidence
  interval from the medians.
- If they differ materially, profile Go JSON validation, admission
  queue waiting, transport connection reuse or SSE forwarding.
  **Choose exactly one change** for a prospective same-endpoint
  baseline/candidate Metriqen-owned code comparison. Collect matched
  reference quality independently. Do not modify any measurement after
  observing a preferred result.
- The existing [P3 optimization-signal gate](portfolio-optimization-signal.md)
  and [controlled comparison](controlled-comparison.md) are for
  baseline/candidate **same gateway endpoint** results; do not feed
  direct-vs-gateway reports into them as if they were a Metriqen
  code optimization or equivalent inference runtimes.
- Even a consistent faster native path is **not** an indication that
  Metriqen can make the model itself generate tokens faster.
  Passing this diagnostic **does not** close P3 or establish causal
  improvement. Actual Go code experiment + matched real benchmark
  + quality and drift review remain necessary.

The complete acceptance scope is
[Portfolio P1–P6](../PORTFOLIO_V1_ACCEPTANCE.md).
