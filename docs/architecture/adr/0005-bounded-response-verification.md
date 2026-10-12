# ADR-0005: Bounded non-streaming chat completion verification

Status: accepted for the **local-only development gateway**.

## Context

The gateway previously forwarded every upstream HTTP 200 JSON response
without checking its body. Malformed completions, an empty choices list or
unbounded output could reach clients as success and corrupt benchmark
success accounting.

## Decision

For non-streaming text chat replies, read at most **2 MiB + 1 byte** before
writing success headers. Accept only one valid JSON document containing at
least one choice whose first message has a **string** content field.
An empty content string is allowed. Do not transform valid response bytes.

Reject malformed, truncated, oversized and unsupported responses with
sanitized HTTP 502 errors. Never include upstream body text in failures.
The request deadline and four-request concurrency cap still apply.

Keep SSE streaming incremental: buffering the entire output to validate
the terminator would destroy TTFT behavior. After headers are flushed,
transport interruptions cannot be turned into an HTTP 502. Streaming
clients must detect missing completion terminators.

## Consequences

- Local text-only inference and benchmark success accounting become safer.
- At most 8 MiB of non-streaming response bytes can be in memory for the
  current four-request process-wide concurrency cap, plus parsing overhead.
- Tool-call-only/multimodal completions with null content are intentionally
  unsupported by this development contract; support requires a separate ADR.
- This is **not** authentication, model verification, GPU optimization,
  a complete OpenAI API implementation or production readiness.

## Verification

Go fixture tests cover valid Unicode and usage preservation, malformed JSON,
empty choices, null/non-string content, oversized/truncated upstream output,
error sanitization and existing streaming/cancellation behavior. The Python
cross-service fixture test checks that invalid HTTP 200 completions become
observable HTTP 502 failures in benchmark results. No real Ollama inference
is claimed from these tests.
