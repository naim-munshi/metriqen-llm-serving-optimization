# ADR-0006: Bounded streaming and success integrity

Status: accepted for the **local-only development** serving path.

## Problem

OpenAI-compatible inference streams deliver HTTP 200 headers before the
model finishes. An upstream may send assistant tokens, then an in-band error,
malformed SSE, a truncated response, or unbounded output. HTTP status alone
does not indicate successful generation. Incorrect success classification
would inflate benchmark throughput and distort latency summaries.

## Decision

The Go gateway continues to forward SSE incrementally, flushing chunks
without waiting for the full response. It stops forwarding after a strict
8 MiB per-request byte budget. This does **not** guarantee a client-visible
HTTP error after the 200 headers were sent; clients must inspect the SSE
stream and its termination.

The local Python benchmark client uses a smaller 1 MiB total stream budget.
It assembles SSE events from successive data fields separated by blank lines,
ignores heartbeat/comments, parses JSON envelopes and rejects malformed
choices, invalid deltas, explicit error events, content-filtered/error
finish reasons, and streams missing the [DONE] terminator. TTFT is measured
only upon the first nonempty content delta. Usage-only events with empty
choices remain supported. Sanitized error categories are written instead
of any upstream error payload or generated text.

On any stream-protocol failure, the request is a **failure**, excluded from
successful throughput and token-usage totals. The synthetic upstream
fixtures test this behavior through the real Go gateway and Python CLI.

## Boundaries

- Gateway buffering stays constant; per-request forwarding is bounded,
  but streaming is not fully validated by the Go gateway.
- A malicious/incorrect server can still send a fake [DONE] event; the
  benchmark does not cryptographically prove a model generated the answer.
- This is **not** real Ollama execution, GPU benchmarking, API authentication
  or production serving.
- Client and gateway streaming limits intentionally differ: 1 MiB for
  reproducible text benchmarks, 8 MiB for bounded local gateway traffic.
