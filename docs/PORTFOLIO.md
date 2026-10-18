# Metriqen — engineering portfolio briefing

**Role focus:** LLM Infrastructure / AI Platform / Backend Engineering  
**Project type:** local open-weight inference research prototype, **not** a commercial production service

**Current portfolio v1.0 definition of done:** [six evidence-based release gates](PORTFOLIO_V1_ACCEPTANCE.md). The original reviewer briefing below describes the historical foundation; newer opt-in JWT/project authorization, Redis quotas, deployment reconciliation and provider-observed streaming usage have since been added. They are **not** automatically active or production-tested. For a real Mac model smoke see [real Ollama acceptance](operations/real-ollama-portfolio-acceptance.md), distinct from the synthetic reviewer demo.

## 60-second explanation

Metriqen is a small, testable LLM serving stack built around a **Go HTTP
gateway** and **Python measurement/evaluation tools**. The gateway forwards
validated chat requests to locally operated Ollama, optionally enforces an
exact model-tag allowlist, and uses a bounded admission scheduler (four
active requests, optional FIFO waiting queue of up to 32). Independent
monitoring endpoints distinguish capacity decisions, Ollama API
reachability, upstream transport/protocol failures and admitted inference
attempt outcomes.

This repository prioritizes verifiable **engineering contracts** and
honest evidence provenance. It does not claim to have invented a faster
LLM inference kernel, continuous batching, vLLM serving, or a
production-grade authenticated multi-tenant platform.

## Reviewer quickstart (no LLM download required)

From the repository root with Python 3.12+ and Go 1.23+:

~~~sh
python3 -m tools.demo.offline
~~~

The command builds and launches the *real gateway* on a temporary loopback
port, alongside a controlled fixture implementing the Ollama-compatible
chat and version endpoints. It checks HTTP streaming, non-stream JSON,
allowlist enforcement, sanitized error propagation, admission
backpressure, request wait handoff, metrics reconciliation and privacy.
It cleans up the processes when finished.

**Expected proof:** a JSON document with `passed: true`,
`checks_passed: 18`,
`model_inference_executed: false`, and
`performance_measurement: false`. These values describe synthetic
contract testing, **not** model-quality or performance measurements.
See the [demo specification](operations/offline-portfolio-demo.md).

For the actual local Ollama route, see [operations](operations/local-inference.md);
the gateway remains **loopback-only**, with optional chat-route shared-secret protection but no production identity.

## Optional shared-secret inference gate

Operators can enable a single static `METRIQEN_GATEWAY_BEARER_TOKEN`
on the local Go chat POST route. The check occurs before inference
admission and credentials are not sent upstream. It is intentionally
**not** the control-plane JWT/RBAC identity boundary, and the local
dashboard/aggregate diagnostics remain unauthenticated.
See [precise setup and threat-model limits](operations/local-bearer-token.md).

## Local Operations Console

Start the loopback-only Go gateway with an explicitly configured
local upstream and open **http://127.0.0.1:8081/console**.

The embedded, read-only dashboard shows the latest gateway readiness,
Ollama API reachability, FIFO occupancy, exclusive inference outcomes,
fixed upstream failure counts and mean duration from admission to
terminal outcome. It does not display TTFT, GPU execution time or
client-observed latency. It uses no external scripts or
analytics. The console is **not** an authenticated management plane,
does not launch generation jobs, and is not a production web service.

A synthetic CI test verifies bundled assets and restrictive CSP headers,
**not browser rendering or performance**. See the
[console runbook](operations/local-console.md).

## Architecture and decisions

| Layer | Implemented and verified | Important limitation |
| --- | --- | --- |
| Data plane | OpenAI-compatible Go chat relay, bounded JSON, incremental SSE, cancellation and deadlines | Optional static local bearer shared-secret, not JWT/RBAC or tenant isolation |
| Admission | Four active requests; optional FIFO pending queue with five-second wait cap, early HTTP 429 | Not GPU batching, priority serving or per-tenant fairness |
| Diagnostics | Process readiness, bounded Ollama API reachability, admission metrics, upstream failure reasons and admitted-attempt outcomes | In-memory, process-local; not full telemetry or persisted SLO evidence |
| Test harness | Go race tests, deterministic mock backends, Python E2E and stand-alone reviewer demo | Synthetic upstream cannot validate actual GPU throughput |
| Benchmark | Pinned-model experiments, workload hashes, rotating concurrency sweeps, TTFT/p50/p95 and token-use coverage | Real runs operator-attested; not independent hardware attestation |
| Quality checks | Answer-reference exact/normalized matching with separate quality reports | Not semantic or hallucination evaluation |
| Control plane | FastAPI identity/RBAC verification seams and optional PostgreSQL-targeted membership adapter | Not wired to Go serving auth; actual PostgreSQL 16 CI migration tests, but not deployed or live IdP-integrated |

**Design tradeoffs:** The gateway accepts a fixed local upstream origin at
startup (no caller-supplied URLs, redirects or environment proxies);
request validation and model filtering occur before admission; the
queue holds only wake-up state, never prompts; output is bounded and
upstream errors are sanitized. These constraints trade feature breadth
for safe, independently testable local behavior.

## Evidence an interviewer can verify

- **Deterministic tests:** GitHub Actions exercises Go formatting,
  `go test -race`, `go vet`, FastAPI tests, Go/Python integration,
  Python benchmark and NLP evaluation contracts. CI does not install
  or benchmark a real LLM.
- **FIFO correctness:** a developer-reported run of four deterministic
  Go tests repeated ten times passed 40/40 invocations with the race
  detector. This is unit/integration evidence, not an inference-speedup result.
- **Ollama outage classification:** a separate local test returned
  sanitized HTTP 502 for an unused upstream loopback port while
  `connection_refused_total` incremented to 1. The original process
  outage's root cause remains unproven.
- **Local model performance observation:** the
  [October 13 case study](benchmarks/experiments/2026-10-13-ollama-parallelism.md)
  reported a +34.235% **descriptive requests-per-second difference**
  at client concurrency two when changing **Ollama's**
  `OLLAMA_NUM_PARALLEL` setting from one to two on one Apple M2.
  It was *not* a performance improvement attributed to Metriqen.
  Raw local evidence is private and the comparison is not causal.
- **Metric accounting:** `GET /metrics/inference` partitions each
  admitted attempt into a single terminal outcome; schema v3 distinguishes standalone terminal SSE `stream_done_total`
  from clean EOF without a marker (`stream_missing_done_total`).
  This checks framing, not JSON events, in-band errors or answer quality.
  See [precise definitions](operations/inference-outcomes.md).

**No performance, uptime, success-rate or model-quality statistic
should be inferred from a fixture test or from a single successful
health request.**

## PostgreSQL tenant integration evidence

An independent GitHub Actions service starts **PostgreSQL 16** and applies
the checked-in tenant-access migration (not a SQLite substitute). Tests
exercise real composite foreign keys, trusted JWT project access,
cross-organization enumeration resistance, membership/grant revocation
and role changes. All identities are synthetic, the database is
disposable, and these tests are **not** public auth or live deployment
evidence. See [database CI runbook](operations/postgres-ci.md).

## Engineering interview discussion prompts

1. **Why does a four-request gateway limit not imply four model runners?**
   Admission capacity and model execution parallelism are different;
   Ollama runtime slots depend on its independent settings and memory.
2. **Why a bounded FIFO queue instead of unbounded buffering?**
   It places an explicit bound on memory and queue wait time, exposes
   backpressure, and avoids silently accumulating pending prompts.
3. **Why is an HTTP 200 not always a valid generated response?**
   A stream may send its headers and fail midflight. Consumers must
   validate SSE termination and in-band errors; the gateway's clean
   transport EOF is not yet a semantic stream completion proof.
4. **How are performance claims separated from implementation claims?**
   Every run records workload, model revision, hardware/runtime and
   warm-up configuration. Synthetic CI checks code contracts; real-model
   operator observations must be reported separately with limitations.
5. **What blocks production exposure?**
   End-to-end authentication at the Go inference route, tenant-scoped
   quotas/isolation, immutable approved model revisions, operational
   monitoring and deployment hardening are still planned.

## Next evidence-driven engineering work

- Add **real-model, matched-config** measurement phases that preserve
  model digest, queue settings, power state, runtime runner slots,
  workload and decoding parameters.
- Decide on a validated SSE end-of-stream semantic contract and
  corresponding gateway-side instrumentation without breaking streaming.
- Implement authenticated inference only after designing actual
  control-plane → gateway identity and tenant quota enforcement.
- Review migration-backed PostgreSQL 16 integration CI, then complete deployment,
  fault recovery, and later GPU-backed serving if suitable hardware is
  available.

The [project status matrix](PROJECT_STATUS.md),
[roadmap](../ROADMAP.md) and [security boundary](../SECURITY.md) are the
sources of truth when evaluating future job-portfolio completeness.
