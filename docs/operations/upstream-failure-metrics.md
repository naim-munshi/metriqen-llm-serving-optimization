# Local upstream failure counters (schema v1)

The Go gateway exposes a **read-only, loopback-only** JSON diagnostic
endpoint at `GET http://127.0.0.1:8081/metrics/upstream`.

It complements the independent `GET /metrics/admission` scheduler
counters (schema v2). The gateway **has no authentication** and must
never be exposed to external networks. These are process-local counters
that reset on gateway restart; there is no persistent history or
external telemetry exporter.

## Inspect

~~~sh
curl -fsS http://127.0.0.1:8081/metrics/upstream | python3 -m json.tool
~~~

Expected initial schema:

~~~json
{
  "schema_version": 1,
  "connection_refused_total": 0,
  "transport_errors_total": 0,
  "timeouts_total": 0,
  "http_rejected_total": 0,
  "http_other_status_total": 0,
  "protocol_errors_total": 0,
  "response_too_large_total": 0,
  "stream_read_errors_total": 0,
  "stream_size_limit_total": 0
}
~~~

## Counters and strict definitions

| Counter | Event counted |
| --- | --- |
| `connection_refused_total` | HTTP transport failed with a typed `ECONNREFUSED` (including wrapped syscall errors), before upstream headers |
| `transport_errors_total` | Other non-timeout transport errors before response headers, excluding client cancellation |
| `timeouts_total` | Observed request-context deadline expiration, typed transport timeout, or streaming/body-read context timeout |
| `http_rejected_total` | Upstream returned HTTP 400, 404 or 429; the gateway preserves those statuses |
| `http_other_status_total` | Other non-2xx upstream HTTP statuses, including redirects and 5xx; the gateway translates these to HTTP 502 |
| `protocol_errors_total` | Invalid upstream response media type or malformed bounded non-streaming completion |
| `response_too_large_total` | Non-streaming completion exceeded the existing 2 MiB response cap |
| `stream_read_errors_total` | Streaming body read failed with a non-EOF error, excluding client cancel or context deadline |
| `stream_size_limit_total` | Streaming bytes would exceed the existing 8 MiB forwarding cap |

Counters increment **after gateway admission**, when a relevant failure
is observed. Validation errors, queue capacity 429s, and queued
cancellations are not upstream failures and are tracked separately,
where appropriate, by `/metrics/admission`.

There is no success/requests-total denominator in this v1 API; do not
calculate success rates or model uptime from these numbers. A failure
counter is **not** a causal crash diagnostic: an `ECONNREFUSED` means
the gateway could not connect at that moment, not that the model was
killed by memory pressure. Protocol/stream failures are also separate
from model answer quality.

The existing HTTP API contract is unchanged. For example, a transport
timeout may still return the gateway's existing sanitized 502, while
a request-context deadline returns 504. The metrics counter is a
separate diagnostic classification, not a mapping of HTTP status code
to root cause.

For streamed responses, HTTP 200 headers can precede a later read or
size-limit error; the stream then terminates rather than retroactively
changing HTTP status. Clean EOF is not currently inspected for SSE
`[DONE]` completeness by the gateway; absence of a stream error
counter does **not** prove a completed streaming generation. Client
disconnects are not counted as upstream failures.

## Privacy and verification

Only nine fixed, unlabelled numeric failure counters plus the schema
version appear in the snapshot. Never add user IDs, model names,
prompts, response bodies, raw error strings, endpoint addresses, or
unbounded labels without a separate design and security review.
Counters use a mutex for concurrent snapshot/record safety.

Deterministic offline tests use fake transports, a mock HTTP upstream
and concurrent counter increments; Go race-enabled tests and CI
verify the contract without invoking Ollama. No model-performance
improvement, Ollama stability or production monitoring reliability
is established by this endpoint.

See [October 14 local FIFO validation](../benchmarks/experiments/2026-10-14-fifo-local-validation.md)
for the original upstream-unavailable observation and its limits.
