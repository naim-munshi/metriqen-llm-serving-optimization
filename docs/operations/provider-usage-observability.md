# Local provider-reported usage telemetry (schema v2)

The **loopback-only Go inference gateway** exposes a read-only,
process-local JSON snapshot:

```sh
curl -fsS http://127.0.0.1:8081/metrics/usage | python3 -m json.tool
```

This endpoint tracks optional **model/provider-reported token counts**
for *completed non-streaming* chat responses and **strictly
certified, cleanly terminated streaming SSE responses**.  This is **not an
independently verified tokenizer count, metered billing, model-runtime
attestation, charged Redis token cap, or guaranteed actual token usage**.
The existing per-project Redis requested-output-cap reservation continues
to charge the full validated `max_tokens` amount **before** forwarding a
request, including on failed/canceled/short outputs. These observation
counters **never refund, settle, or alter** that budget.

## Snapshot contract

A freshly started gateway (schema v2) returns zeros:

```json
{
  "schema_version": 2,
  "classified_terminal_attempts_total": 0,
  "nonstream_provider_plausible_total": 0,
  "nonstream_provider_missing_total": 0,
  "nonstream_provider_malformed_total": 0,
  "nonstream_provider_inconsistent_total": 0,
  "nonstream_provider_above_cap_total": 0,
  "nonstream_no_completion_total": 0,
  "stream_not_measured_total": 0,
  "stream_provider_plausible_total": 0,
  "stream_provider_missing_total": 0,
  "stream_provider_malformed_total": 0,
  "stream_provider_inconsistent_total": 0,
  "stream_provider_above_cap_total": 0,
  "stream_incomplete_total": 0,
  "provider_prompt_tokens_reported_total": 0,
  "provider_output_tokens_reported_total": 0,
  "provider_all_tokens_reported_total": 0
}
```

Every **terminal admitted attempt** contributes to exactly one disjoint
counter:

- `nonstream_provider_plausible_total`: bounded successful, **fully
  written** non-stream completion with all three self-reported integer
  fields `usage.prompt_tokens`, `usage.completion_tokens` and
  `usage.total_tokens`; each is nonnegative; total equals prompt +
  completion; prompt ≤1,000,000; output ≤2,048 **and** output ≤the
  sanitized request's validated `max_tokens` (default 256). *Plausible
  only means internally consistent, not correct or trusted.*
- `nonstream_provider_missing_total`: successful non-stream completion
  had no `usage` field.
- `nonstream_provider_malformed_total`: successful non-stream
  completion's `usage` was malformed, had missing keys, null/negative,
  booleans, strings, fractional/exponential numeric types, or invalid
  integer representation.
- `nonstream_provider_inconsistent_total`: parseable nonnegative
  integers had mismatched totals or exceeded fixed sanity bounds.
- `nonstream_provider_above_cap_total`: internally consistent
  reported token counts claimed **more generated tokens than the
  validated requested output cap**. This is a *discrepancy signal*,
  not evidence of an actual token budget violation.
- `nonstream_no_completion_total`: admitted non-stream attempt
  never finished a complete downstream response write, including
  provider errors, network problems, cancellation, invalid JSON,
  overflow and writer interruption.
- `stream_provider_plausible_total`: a clean SSE transport EOF with a
  standalone `data: [DONE]` terminal event and exactly one final
  bounded (maximum **4 KiB data line**) JSON event with
  `"choices":[]` and internally consistent `"usage"` metadata.
  That usage event must be the **last data event before [DONE]**.
  These observations remain **provider self-reports**, not verified usage.
- `stream_provider_missing_total`: completed SSE stream and terminal
  marker, but no admissible final usage event was observed.
- `stream_provider_malformed_total`: terminal stream with malformed
  usage JSON, incorrect `choices` shape, or duplicate/late usage
  events. SSE events with multiple `data:` lines or an oversized
  usage line are not accepted as evidence.
- `stream_provider_inconsistent_total`: final usage event's parseable
  token counts conflict or exceed absolute sanity bounds.
- `stream_provider_above_cap_total`: a final internally consistent
  report claims more output tokens than the validated `max_tokens` cap.
- `stream_incomplete_total`: streaming attempt failed, was canceled,
  exceeded transport limits, had clean EOF **without** valid terminal
  `[DONE]`, or had data after the terminal marker. No token totals
  are accepted for these attempts.
- `stream_not_measured_total`: **legacy-compatible non-plausible
  streaming subtotal**, equal to the five missing/malformed/
  inconsistent/above-cap/incomplete counts just described. It
  does **not** include `stream_provider_plausible_total` in schema v2.

The three `provider_*_tokens_reported_total` values include bounded,
internally consistent, **self-reported** counts from completed
non-streaming responses AND qualifying completed SSE streams. Missing,
malformed, contradictory, over-cap, canceled or interrupted observations
contribute zero, which **does not mean** the model generated zero tokens.
Importantly, usage claims are never used to settle, deduct, or refund
the separately enforced full-cap Redis reservation.

Two invariants should always hold on an idle or concurrent snapshot:

```text
classified_terminal_attempts_total =
  nonstream_provider_plausible_total + nonstream_provider_missing_total
  + nonstream_provider_malformed_total
  + nonstream_provider_inconsistent_total
  + nonstream_provider_above_cap_total
  + nonstream_no_completion_total + stream_not_measured_total
  + stream_provider_plausible_total

stream_not_measured_total =
  stream_provider_missing_total + stream_provider_malformed_total
  + stream_provider_inconsistent_total + stream_provider_above_cap_total
  + stream_incomplete_total

provider_all_tokens_reported_total =
  provider_prompt_tokens_reported_total + provider_output_tokens_reported_total
```

`classified_terminal_attempts_total` is comparable to the existing
`/metrics/inference` `attempts_total - active_attempts` **after
requests have finished**; the two independently synchronized metrics
endpoints are **not transactionally consistent** if concurrently polled.

## Privacy and trust boundaries

For non-stream completions, the existing **2 MiB** response-body
bound and single JSON envelope parse are preserved. For streaming SSE
bodies, the existing **8 MiB** transport cap remains; a separate
bounded per-request observer retains at most two **4 KiB** transient
buffers for event framing. It only inspects forwarded bytes and
never buffers an entire stream. A model can hide usage in an oversized
event or encode keys unusually; the safe result is then *unmeasured*,
not a fabricated count. Only bounded integers and fixed counters are
retained beyond the request;
no prompt, response text, model tag, token, user ID, project ID, runtime
error, or raw usage JSON is stored or reflected by `/metrics/usage`.
The endpoint is read-only with `Cache-Control: no-store`; a gateway
must still bind to loopback and not be exposed to a public interface.

The counters reset on gateway restart and **do not** persist across
replicas. They are not suitable for authoritative user consumption or
financial reconciliation. A malicious or buggy Ollama-compatible
upstream can fabricate perfectly consistent token totals, or omit them
entirely. No independent prompt tokenization verification,
token-by-token usage derivation, attestation of runtime versions
or model digest at the response,
cryptographic usage signatures, billing ledger, cost conversion,
refund/reconciliation workflow or cross-replica durable aggregation
is implemented. A genuine multi-tenant usage bill requires substantially
stronger evidence and data governance.

Existing synthetic tests cover missing, malformed, negative,
float, overflow, contradictory totals, above-cap reports,
duplicate/late final usage events, spoofed [DONE] markers, LF/CRLF,
arbitrary SSE chunk boundaries, oversized lines, private text
exclusion, malformed upstream responses, failed downstream writes
and simultaneous counter updates under Go's race detector. These are **synthetic local CI tests, not evidence of real Mac
Ollama output usage, TTFT, GPU efficiency or production usage accuracy**.
