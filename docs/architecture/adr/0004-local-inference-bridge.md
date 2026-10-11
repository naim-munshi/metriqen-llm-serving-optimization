# ADR-0004: Local-only streaming inference gateway vertical slice

- Status: Accepted for development
- Date: 2026-10-12

## Context

Metriqen has healthy control/gateway processes and domain-level security primitives, but no observable inference. Continuing to add isolated security modules without exercising the inference data plane delays the core engineering risk: maintaining low-latency streaming semantics and containing runtime failure under concurrent traffic.

## Decision

- Implement a small `/v1/chat/completions` endpoint in the existing Go gateway, targeting one configured local Ollama-compatible runtime. The API shape is also compatible with vLLM's chat endpoint; GPU/vLLM integration itself is not claimed.
- Disable inference by default. Reject remote/user-supplied upstream URLs. Do not forward credentials or follow redirects; do not use an outbound HTTP proxy.
- Validate incoming request shape, impose a bounded body size and generation cap, and fail closed with structured errors.
- Use a maximum of four in-flight requests per process. Cancel upstream work on disconnect and flush SSE events as they arrive. Do not buffer an entire streamed response.
- Preserve existing control-plane health checks and their process-level meaning.
- Use simulated local HTTP upstreams in integration tests before publishing. Run tests with the race detector. Do not claim actual model serving or GPU results until a real runtime is connected and verified.

## Alternatives considered

- Run vLLM in the developer's Apple Silicon M1 environment: not suitable for NVIDIA CUDA/GPU benchmarks; deferred until validated GPU hardware is available.
- Introduce Kubernetes or distributed queues now: unnecessary complexity before the basic inference path works.
- Forward arbitrary user-supplied URLs: rejected due to SSRF and private data exfiltration risk.
- Implement an HTTP reverse proxy without request validation or concurrency limits: unsafe for a local LLM with finite compute/memory.

## Follow-up production gates

OIDC/API keys and authorization on every inference request, per-project quotas and admission control, TLS/service identities, telemetry and tracing with prompt redaction, model registry and immutable revisions, graceful deploy/rollback, and trusted vLLM deployment in isolated GPU compute.
