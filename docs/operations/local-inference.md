# Local inference bridge

Metriqen's Go gateway can relay OpenAI-compatible chat requests to a **locally operated** inference runtime, such as Ollama. This is a limited development vertical slice, not an internet-facing inference service and not an LLM deployment system.

## Acceptance criteria

| Requirement | Verification |
| --- | --- |
| No upstream configured -> no inference | `TestInferenceDisabledByDefault` (`503`) |
| Non-streaming completion forwarded to fixed local runtime | `TestForwardsNonStreamingCompletionAndSanitizesHeaders` |
| Streaming output delivered before completion | `TestStreamingFlushesFirstEventBeforeCompletion` (real loopback HTTP test) |
| Bad JSON, missing fields, too many tokens and excessive request size rejected | `TestInvalidRequestsRejectedWithoutCallingUpstream` |
| Upstream failure and protocol mismatch return bounded, sanitized errors | `TestUpstreamErrorsAndRedirectsNeverLeakResponseBody`, `TestProtocolMismatchRejected` |
| Malformed, truncated or oversized non-streaming completions do not become HTTP 200 | `TestGatewayRejectsMalformedNonStreamingCompletions`, `TestGatewayRejectsTruncatedUpstreamBody`, `TestReadValidatedCompletion` |
| Maximum four outstanding inferences; release slots | `TestConcurrencySaturationReturns429AndRecovers` |
| Client disconnect cancels upstream | `TestClientCancellationStopsUpstream` |
| Unknown/external inference server URL rejected | `TestRejectsUnsafeUpstreamOrigins` |

**Unverified in this milestone:** end-to-end generation with a real Ollama-installed model, GPU inference using vLLM, TTFT/throughput/quality measurements, authentication, project quotas, worker scheduling, deployment orchestration.

## Run locally with Ollama

1. Install and start Ollama. Select/pull an open-weight model that fits your hardware and license requirements (example: `ollama pull llama3.2:1b`). Verify it answers using Ollama directly before starting the gateway. Do not use a remote/cloud Ollama endpoint here.
2. From `services/inference-gateway`, run:

   ```bash
   METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 go run ./cmd/server
   ```

3. Send a non-streaming test request:

   ```bash
   curl -sS --fail-with-body -X POST http://127.0.0.1:8081/v1/chat/completions \
     -H 'Content-Type: application/json' \
     -d '{"model":"llama3.2:1b","messages":[{"role":"user","content":"Explain batching in one sentence."}],"max_tokens":100,"stream":false}'
   ```

4. Verify server-sent events arrive as chunks:

   ```bash
   curl -N -sS http://127.0.0.1:8081/v1/chat/completions \
     -H 'Content-Type: application/json' \
     -d '{"model":"llama3.2:1b","messages":[{"role":"user","content":"Count to five."}],"max_tokens":100,"stream":true}'
   ```

Run automated checks from `services/inference-gateway`: `go test -race ./...` and `go vet ./...`.

## Optional exact model-tag allowlist (loopback development only)

Operators can opt in to an exact, case-sensitive **model-tag filter** at
gateway startup without changing the default behavior:

~~~sh
cd services/inference-gateway
METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 \
METRIQEN_ALLOWED_MODELS=llama3.2:1b \
go run ./cmd/server
~~~

A comma-separated list of up to 32 local model names is supported, such as
llama3.2:1b,nomic-embed-text:latest. The variable is optional; absent or
empty means there is **no model-name filter**, exactly as before. Nonempty
invalid configuration (whitespace, unsupported characters, duplicates,
empty names, oversized lists) blocks gateway startup. Disallowed model names
return HTTP 403 with a bounded model_not_allowed error **before** any prompt
is forwarded upstream or consumes an inference concurrency slot. The
response does not reveal the permitted model list.

This is *not authentication, access control per user or project, or a
cryptographic model revision pin*. Ollama model tags can be moved to point
to different underlying digests. Independently verify the immutable model
digest before benchmark phases using the existing preflight and evidence
protocol. This filtering does not make the unauthenticated gateway safe to
expose outside loopback and does not replace future Phase 04 model registry.

## Optional bounded FIFO admission queue (development only)

By default, the gateway immediately returns HTTP 429 when its four local
inference slots are occupied. No waiting queue is created, preserving the
baseline used by existing benchmarks.

For a **separate, explicitly labeled** scheduling experiment, restart the
local gateway with a strictly bounded waiting queue:

~~~sh
cd services/inference-gateway
METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 \
METRIQEN_QUEUE_DEPTH=2 \
go run ./cmd/server
~~~

- Queue depth accepts a canonical decimal integer **0 through 32**; unset
  or 0 means no queue. Invalid settings fail startup rather than silently
  changing the experiment. Depth counts waiting requests only, not active.
- At most four requests are forwarded to the configured local upstream
  at once. A released slot is handed to the oldest queued request.
- A waiting request is bounded by a **5-second admission deadline**.
  A full queue returns HTTP 429 `capacity_exceeded` and `Retry-After: 1`.
  Expired waits return HTTP 429 `queue_wait_timeout` and the same retry
  hint. Client disconnect removes the queued waiter without forwarding
  its prompt; canceled handoffs release their reservation safely.
- The queue stores a small wake-up channel per request. It does not persist
  prompts, perform batching, schedule tokens, predict token cost, or provide
  priority or multi-tenant fairness.
- **Changing queue depth changes the experimental system.** Do not compare
  historical A1 throughput/TTFT to a queue-enabled gateway as though
  all serving settings were equal. Keep the default (depth 0) for the
  previously defined parallelism-only A/B/A/B study.

This is still an **unauthenticated loopback-only gateway**. Queuing does
not make it secure for public traffic, allocate GPU memory or replace
project-level quotas and admission controls.

## Admission observability (local JSON endpoint)

The gateway exposes `GET /metrics/admission` as a **process-local**
diagnostic. It returns a schema-versioned JSON snapshot without prompt text,
output, model name, user identity, IP addresses or per-request labels:

~~~sh
curl -fsS http://127.0.0.1:8081/metrics/admission | python3 -m json.tool
~~~

The exact field names are:

| Fields | Interpretation |
| --- | --- |
| `active_requests`, `waiting_requests` | Current active gateway admissions and FIFO waiters (not GPU execution slots) |
| `max_active_requests`, `max_waiting_requests` | Configured gateway limits |
| `admitted_immediate_total`, `admitted_queued_total` | Requests successfully **admitted** immediately / after waiting; **not** successful inference completions |
| `rejected_full_total` | Requests rejected because active capacity or bounded queue was full |
| `queue_timeout_total`, `queue_canceled_total` | Requests that **entered the queue** but failed admission by deadline or client cancellation |
| `queue_wait_observations_total` | Number of queue entrants whose wait has **ended** (admitted, timed out or canceled) |
| `queue_wait_duration_total_us` | Sum of completed queue wait durations in **microseconds** (divide by observations for a descriptive mean) |
| `queue_wait_bucket_upper_bounds_ms` | Inclusive upper bounds for fixed, mutually exclusive wait buckets: 10, 50, 100, 250, 500, 1000, 2000, 5000 ms |
| `queue_wait_bucket_counts` | Nine corresponding **noncumulative** counts: eight bounded buckets and one overflow bucket above 5000 ms |
| `schema_version` | Current response format revision (2) |

Metrics are aggregate, in-memory, monotonic counters, a **fixed-size
queue-wait histogram**, and instantaneous gauges. The wait histogram records
the enqueue-to-outcome time for **completed queue waits only**; it includes
waits ending in admission, timeout or client cancellation. It does not
include rejected-full requests (which never entered the queue) or immediate
admissions. Bucket counts sum to `queue_wait_observations_total`; totals
include all completed queued outcomes only, and reset at process restart.
The microsecond-sum is intended for aggregate descriptive averages; fixed
histogram buckets cannot reconstruct exact percentiles. Queue-wait duration
is **not** TTFT or end-to-end model latency. Metrics do **not** measure
inference completion success, GPU utilization, response correctness,
model-level concurrency, or scheduler speedup. The initial response is useful even when
inference is disabled (zero active requests). `POST /metrics/admission` is
rejected; all responses are `Cache-Control: no-store`.

This endpoint is intended for **loopback development use only**, like the
unauthenticated inference gateway. Do not port-forward or publish it publicly;
the current deployment is not production-hardened. A production observability
stack would require authorization, stable metric naming/collection and
carefully designed export paths. For historical benchmarking, record the
gateway revision and metrics schema if observing the scheduler; this endpoint
does not retroactively validate previous Ollama parallelism experiments.

## Contract and constraints

- Supported endpoint: **POST `/v1/chat/completions`**, JSON body only, chat messages and non-empty model required.
- Default output cap: 256 tokens; maximum requested output: 2,048 tokens. Up to 32 chat messages and 1 MiB request body.
- Maximum four active inference requests per process. By default additional requests return HTTP 429 with `Retry-After: 1`; an explicitly enabled bounded FIFO queue may wait up to five seconds for capacity.
- Upstream request lifetime limited to 120 seconds; upstream header timeout 30 seconds; SSE data is flushed as it arrives. Client disconnect propagates cancellation.
- Upstream URLs are startup configuration only, not supplied by users. Only `http://127.0.0.1:PORT`, `http://localhost:PORT`, `http://[::1]:PORT`, or container-only `http://ollama:PORT` are permitted, with no path, query, or credentials. External and HTTPS endpoints are deliberately unsupported in this **development-only** slice.
- When inference is enabled, the gateway refuses a non-loopback listener, including `0.0.0.0`; the health-only Docker Compose service is unaffected.
- The gateway does not forward caller-provided Authorization, Cookie, Host, or arbitrary headers upstream. It does not log prompt text. No redirect following and no proxy-from-environment.
- Non-streaming successful completions are checked before returning HTTP 200: up to 2 MiB of JSON, nonempty `choices`, and a first assistant message with a string `content` field. Empty text strings are allowed; non-text/tool-call-only replies are **not** in this milestone's contract. Malformed, truncated and oversized completions return sanitized HTTP 502 errors without forwarding upstream text. Valid non-streaming JSON is forwarded unchanged.
- SSE streaming remains incremental rather than fully buffered and has an **8 MiB per-request forwarding budget**. Once success headers are sent, upstream interruption, an excessive response or malformed event cannot be turned into a new HTTP error code; clients must verify the expected `[DONE]` terminator and handle in-band errors. The benchmark client rejects these incomplete/error streams.
- Upstream JSON/error responses are not guaranteed to be byte-for-byte preserved: requests are validated and re-encoded, and upstream error messages are sanitized.
- `/health/ready` indicates the gateway process is responsive. The independent [on-demand upstream health probe](upstream-health-probe.md) at `GET /health/upstream` checks Ollama's lightweight HTTP version API with a two-second deadline; it does **not** prove a model is loaded or inference will succeed.
- Docker Compose exposes host ports on loopback; inference remains disabled in Compose unless explicitly configured. A host-native Go process is simplest when Ollama runs natively on macOS.

## Security boundary

**Never expose the inference route publicly.** It has no user authentication, tenant isolation, per-user quotas, transport security, API-key enforcement, prompt retention controls, or audit logs. The existing OIDC and RBAC libraries are not connected to this route. A local machine can run this gateway for development only. Public/Kubernetes deployment is blocked until separate security and operations milestones are completed.

## Future: measurable benchmarks

For each run, pin the model revision, quantization settings, runtime, hardware, prompt and generated token counts, concurrency profile and warm-up. Capture TTFT, p95 latency, token throughput, error rates and cost assumptions using an independent benchmark runner. Integration-test timing is **not** a performance benchmark and must not be used as evidence of optimization.
