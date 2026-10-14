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

## Contract and constraints

- Supported endpoint: **POST `/v1/chat/completions`**, JSON body only, chat messages and non-empty model required.
- Default output cap: 256 tokens; maximum requested output: 2,048 tokens. Up to 32 chat messages and 1 MiB request body.
- Maximum four concurrent inference requests across this one gateway process. Additional requests return HTTP 429 with `Retry-After: 1`.
- Upstream request lifetime limited to 120 seconds; upstream header timeout 30 seconds; SSE data is flushed as it arrives. Client disconnect propagates cancellation.
- Upstream URLs are startup configuration only, not supplied by users. Only `http://127.0.0.1:PORT`, `http://localhost:PORT`, `http://[::1]:PORT`, or container-only `http://ollama:PORT` are permitted, with no path, query, or credentials. External and HTTPS endpoints are deliberately unsupported in this **development-only** slice.
- When inference is enabled, the gateway refuses a non-loopback listener, including `0.0.0.0`; the health-only Docker Compose service is unaffected.
- The gateway does not forward caller-provided Authorization, Cookie, Host, or arbitrary headers upstream. It does not log prompt text. No redirect following and no proxy-from-environment.
- Non-streaming successful completions are checked before returning HTTP 200: up to 2 MiB of JSON, nonempty `choices`, and a first assistant message with a string `content` field. Empty text strings are allowed; non-text/tool-call-only replies are **not** in this milestone's contract. Malformed, truncated and oversized completions return sanitized HTTP 502 errors without forwarding upstream text. Valid non-streaming JSON is forwarded unchanged.
- SSE streaming remains incremental rather than fully buffered and has an **8 MiB per-request forwarding budget**. Once success headers are sent, upstream interruption, an excessive response or malformed event cannot be turned into a new HTTP error code; clients must verify the expected `[DONE]` terminator and handle in-band errors. The benchmark client rejects these incomplete/error streams.
- Upstream JSON/error responses are not guaranteed to be byte-for-byte preserved: requests are validated and re-encoded, and upstream error messages are sanitized.
- `/health/ready` indicates the process is responsive; **it does not mean a model is loaded or that the upstream is reachable**.
- Docker Compose exposes host ports on loopback; inference remains disabled in Compose unless explicitly configured. A host-native Go process is simplest when Ollama runs natively on macOS.

## Security boundary

**Never expose the inference route publicly.** It has no user authentication, tenant isolation, per-user quotas, transport security, API-key enforcement, prompt retention controls, or audit logs. The existing OIDC and RBAC libraries are not connected to this route. A local machine can run this gateway for development only. Public/Kubernetes deployment is blocked until separate security and operations milestones are completed.

## Future: measurable benchmarks

For each run, pin the model revision, quantization settings, runtime, hardware, prompt and generated token counts, concurrency profile and warm-up. Capture TTFT, p95 latency, token throughput, error rates and cost assumptions using an independent benchmark runner. Integration-test timing is **not** a performance benchmark and must not be used as evidence of optimization.
