# Inference outcome metrics (schema v3)

The local Go gateway exposes read-only, process-local counters at
`GET http://127.0.0.1:8081/metrics/inference`. They describe **the
gateway's HTTP inference attempts**, not inference correctness,
GPU utilization, answer quality, or model throughput.

~~~sh
curl -fsS http://127.0.0.1:8081/metrics/inference | python3 -m json.tool
~~~

An idle, newly started gateway returns:

~~~json
{
  "schema_version": 3,
  "attempts_total": 0,
  "active_attempts": 0,
  "nonstream_complete_total": 0,
  "stream_clean_eof_total": 0,
  "stream_done_total": 0,
  "stream_missing_done_total": 0,
  "pre_response_failure_total": 0,
  "post_response_interrupted_total": 0,
  "client_canceled_total": 0,
  "duration_observations_total": 0,
  "duration_total_us": 0,
  "duration_bucket_upper_bounds_ms": [100, 250, 500, 1000, 2500, 5000, 10000, 30000],
  "duration_bucket_counts": [0, 0, 0, 0, 0, 0, 0, 0, 0]
}
~~~

## Outcome contract

`attempts_total` increases **once after a request successfully acquires
gateway admission** (including immediate and queued admissions).
The `active_attempts` gauge increases at that moment and returns to
zero when the handler finishes. Exactly one terminal counter increments
for each completed admitted request:

| Field | Precise event |
| --- | --- |
| `nonstream_complete_total` | A bounded, schema-validated non-stream JSON completion was entirely written to the downstream `http.ResponseWriter` without a reported write error |
| `stream_clean_eof_total` | SSE upstream body reached clean `io.EOF` after forwarding received chunks; this is the sum of `stream_done_total` and `stream_missing_done_total` |
| `stream_done_total` | After clean upstream EOF, gateway observed one standalone SSE `data: [DONE]` event and no subsequent data events; transport-level terminal marker only |
| `stream_missing_done_total` | Clean upstream EOF without a confirmed terminal marker (including truncated or spoofed terminal framing). This is **not** a valid completed SSE generation |
| `pre_response_failure_total` | Handler finished after admission without reaching a validated non-stream completion or sending streaming HTTP 200 headers (e.g., upstream connection refused/429/503 or response protocol invalid) |
| `post_response_interrupted_total` | After sending HTTP 200, the gateway observed downstream write failure, upstream streaming read failure, stream byte limit, or timeout (except caller cancellations) |
| `client_canceled_total` | After admission, the caller's request context was canceled before the handler completed |
| `active_attempts` | Current admitted requests whose outcome has not yet been recorded; not actual GPU/runner execution parallelism |

**Accounting invariants** for every consistent snapshot:

~~~text
attempts_total = active_attempts + nonstream_complete_total
               + stream_clean_eof_total + pre_response_failure_total
               + post_response_interrupted_total + client_canceled_total
stream_clean_eof_total = stream_done_total + stream_missing_done_total
duration_observations_total = attempts_total - active_attempts
duration_observations_total = sum(duration_bucket_counts)
~~~

Invalid/malformed input, allowlist HTTP 403, full-queue HTTP 429,
queue wait timeout and cancel **before admission** do not increment
these attempt counters; consult the independent
[`/metrics/admission`](local-inference.md) endpoint. This endpoint
does not change the gateway's existing HTTP status codes or scheduling.

## Gateway-observed handler duration (schema v3)

The additional duration metrics use Go's monotonic elapsed time
from **successful admission immediately before the inference attempt starts**
until its handler returns and records one terminal outcome.
This includes upstream HTTP transport, model response production,
non-stream response validation, streaming until EOF/interruption, and
downstream writes; it **excludes** input validation and FIFO queue
waiting before admission. It is *not* time to first token (TTFT),
pure model execution time, client-observed end-to-end latency, or
GPU kernel timing.

- `duration_observations_total`: one sample for **every terminal admitted
  attempt**, including upstream failures and client cancellation.
  Active attempts have no duration sample until they finish.
- `duration_total_us`: sum of completed durations in integer microseconds
  (each sample truncated to the nearest whole microsecond).
- `duration_bucket_upper_bounds_ms`: fixed ascending boundaries
  `[100,250,500,1000,2500,5000,10000,30000]`.
- `duration_bucket_counts`: nine **disjoint** buckets: eight at-or-below
  their respective upper bounds and a ninth for durations strictly
  **greater than 30 seconds**. Counts reset on restart.
- Average handler duration may be computed as
  `duration_total_us / duration_observations_total / 1000` milliseconds
  when the denominator is nonzero, but combines all outcome types.
  Do **not** claim exact p50/p95, successful-inference-only latency,
  uptime, real-model speedup, or production SLO from these aggregates.

The UI displays this all-outcome mean, labeled accordingly. For real
model latency and throughput comparisons use a pinned workload,
actual request-level benchmark measurements and separate failure
accounting. A synthetic fixture duration is not model evidence.

## Evidence and limitations

- These counters are **mutually exclusive per handler completion**,
  not an availability or correctness metric; use a known observation
  window and a suitable benchmark client for output validation.
- A gateway stream without a valid terminal marker still has clean network
  EOF; `stream_missing_done_total` identifies that condition. Marker detection
  is **constant-memory** and robust to chunk boundaries, SSE comments,
  CRLF and marker-looking JSON text, but does not validate JSON envelopes,
  model answer correctness or in-band errors. Always validate SSE semantics
  separately using the Python benchmark client before claiming success.
- A successful non-stream writer call does not prove the remote client
  actually received all bytes, nor that the language model's answer
  was correct. A canceled request has no automatically inferred model
  success/failure.
- Counts reset on gateway restart and are not persisted, exported
  externally or labeled by model, project, user, prompt or error body.
  A reported sum is *process-local*, not a multi-instance fleet total.
- All snapshots use a mutex to protect concurrent increments and
  gauges. The route is read-only, returns `Cache-Control: no-store`
  and remains within the **unauthenticated loopback-only** gateway.
  Never expose it publicly.
- The independently versioned `/metrics/upstream` endpoint records
  *failure reasons*. It is not a mutually exclusive outcome partition
  or denominator, and its values must not be arithmetically added
  to these counters.

Tests use deterministic fake upstreams, a failing downstream writer,
client cancellations and concurrency saturation. No real Ollama
performance or production uptime is inferred from fixture results.

See also [upstream failure metrics](upstream-failure-metrics.md) and
[offline portfolio demo](offline-portfolio-demo.md).
