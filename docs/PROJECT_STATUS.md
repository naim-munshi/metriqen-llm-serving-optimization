# Metriqen — implementation and evidence status

**As of October 14, 2026.** This is a local engineering prototype, not a production LLM deployment. Current statements distinguish implemented code, test coverage, operator observations, and planned components.

## Scope matrix

| Component | What exists | Verification | Not established |
| --- | --- | --- | --- |
| Reproducible portfolio demo | One-command `python3 -m tools.demo.offline` starts the real Go gateway with a deterministic loopback-only synthetic model fixture and checks 18 contracts; no model install or model inference | Offline CLI regression in `local-inference-e2e` CI; assertions include FIFO backpressure, sanitized failures and privacy | Real-model performance, production readiness, GPU inference, measured optimization |
| Local operations console | Embedded read-only, responsive `GET /console` UI and bundled CSS/JS showing existing health, capacity, outcome and failure aggregates | Go asset/CSP/read-only tests and one synthetic offline E2E asset check | Browser-level screenshot/accessibility audit, authenticated UI, production telemetry, persistent time series |
| Opt-in local inference bearer gate | `METRIQEN_GATEWAY_BEARER_TOKEN` (32–256 printable ASCII) protects only the loopback chat POST via a constant-time shared-secret check before validation/admission; default unchanged | Go startup/auth/anti-forwarding tests and secondary real-Go offline synthetic auth fixture | JWT control-plane integration, tenant isolation, public authentication, key rotation, TLS, multi-user quotas |
| Local model registry / preload | Opt-in FastAPI routes and responsive browser Model Manager for local Ollama discovery, SQLite tag/digest approval, preload/unload, and a bounded approved-model single-turn chat path to the Go gateway | Synthetic HTTP, SQLite, static asset and CSP tests in control-api CI; separate operator runbook | No real-model Mac inference/preload attestation, browser visual QA, Go gateway-side approval enforcement, streaming chat UI, production deployment, immutable Ollama tag locking, or tenant identity integration |
| Go inference gateway | OpenAI-compatible local chat relay, SSE, input/output limits, timeout, cancellation, four active admissions | Unit tests, race-enabled CI, synthetic loopback E2E, developer-reported local Ollama test | Authenticated public endpoint or GPU serving |
| Upstream failure observability | Read-only process-local `GET /metrics/upstream` (v1) with fixed sanitized transport, timeout, HTTP status, protocol and stream-failure counters | Offline fake-transport regression tests and race-enabled CI | Cause of earlier Ollama process exit, process monitoring, streaming `[DONE]` completion guarantees, uptime or production service-level metrics |
| Upstream API reachability | Explicit, read-only `GET /health/upstream` checks bounded local Ollama `/api/version` response with 2-second timeout and sanitized 200/503 status | Deterministic probe, redirect, timeout, malformed-body and cancellation tests (CI required) | Model loaded/inference ready, automated Ollama recovery, crash/root-cause diagnosis or SLA |
| Inference outcomes | Read-only, process-local `GET /metrics/inference` (v3) tracks admitted attempts, exclusive terminal outcomes, current active gauge, bounded post-admission duration histogram and constant-memory SSE terminal marker breakdown | Go mock upstream, cancellation, stream interruption and race tests; offline demo | Model accuracy, full SSE JSON/in-band error validation, client receipt guarantees, fleet aggregation or GPU throughput |
| Request admission | Opt-in FIFO queue (0–32 pending, 5-second wait cap), default immediate 429, per-process cancellation, backpressure, aggregate `GET /metrics/admission` snapshot and fixed queue-wait histogram | Go race-enabled tests, HTTP fixtures and counter/queue-state regression tests | Batching, fairness between tenants, inference completion metrics, speedup, GPU memory optimization |
| Model allowlist | Optional strict model **tag** filtering before upstream | Negative/configuration tests | Immutable approved model digests |
| Benchmark runner | Versioned workload, rotating concurrency 1/2/4 repeated sweeps, TTFT, p50/p95, success rates, token usage, optional UTC run timing | Python unit tests and private operator-run measurements | Cloud/GPU-attested measurements, broad workload representativeness |
| Evaluator | 36-question starter quality workload, exact and normalized match | Synthetic CI tests, separately reported local model results | Semantic/factuality/hallucination or safety grading |
| Experiment analysis | Pairwise comparison; A/B/A and A/B/A/B summaries; consistency/evidence gates; local review reports | Python CI fixtures, human-attested local raw reports | Causal attribution or reliable population-p95 uncertainty intervals |
| FastAPI control service | Health, fail-closed pinned JWKS access-token identity, role-scoped project-read seams | Python tests with mocked/synthetic identities | Production login, full tenant CRUD or authenticated Go inference |
| Relational memberships | PostgreSQL-targeted schema, checked-in privileged migration and optional SQLAlchemy adapter | SQLite-backed contract tests plus **separate real PostgreSQL 16 migration-backed CI** with JWT project access, composite FK rejection, cross-tenant isolation and revocation tests | Production migrations, live IdP, deployed database, production tenant isolation |
| Docker Compose | Loopback host mappings and local health-only services | Configuration committed | Complete local Docker validation across machines |
| Production platform | Roadmap only | — | vLLM, GPU batching, cost-aware scheduling, observability, HA, multi-tenant APIs, web console |

## Research evidence separation

### Published, operator-attested case study — October 13, 2026

[Parallelism 1 vs 2](benchmarks/experiments/2026-10-13-ollama-parallelism.md) describes two sequential Ollama 0.30.8 configurations with a pinned `llama3.2:1b` revision on an Apple M2 (8 GiB, Metal), 648 successful measured requests total, and 36 reference questions per configuration. At offered client concurrency 2, the observed change in requests/s was **+34.235%**. At concurrency 1, requests/s worsened **−3.895%**. These are descriptive observations of **Ollama's configured parallelism**, not the gateway's new FIFO queue.

CI validates public transcription arithmetic and synthetic report contracts, not the private raw inference measurements. No causal or general optimization claim is justified.

### Unpublished operator-local follow-ups

Additional Oct 13 A2 and Oct 14 A1 sweeps, variability checks, and a separate battery-powered 36-question quality evaluation are reported by the developer. They were not consolidated into a controlled, public four-phase A/B/A/B dataset, and the October 14 battery quality run must **not** be presented as a matched AC-controlled experiment. Reports remain Git-ignored under `reports/local/`.

No new public benchmark figure should be added unless its provenance and limitations are independently checked against source reports.

### Model-quality regression smoke — October 14, 2026

A developer-reported [isolated arithmetic check](nlp-eval/observations/2026-10-14-arithmetic-model-smoke.md)
found `llama3.2:1b` answered a `2+2` question as `2` in three
native Ollama API calls (0/3 reference matches), while
`llama3.2:latest` returned `4` in three (3/3). A separate one-off
`7+5` native request was correct for `llama3.2:1b`.
This is a narrow, operator-reported, uncontrolled local observation.
A distinct two-question fixture and **synthetic CI scoring contract**
were added without rewriting historical benchmark results or treating
a model's live answers as reproducible CI assertions.

### FIFO admission and upstream-health smoke — October 14, 2026

The developer reported [local FIFO scheduler validation](benchmarks/experiments/2026-10-14-fifo-local-validation.md): four existing mock-upstream tests passed ten iterations each with Go's race detector (40 passing test invocations, no race reported). An exploratory seven-request Ollama burst gave four immediate admissions, two queued admissions and one full-queue rejection, **but six HTTP 502s**; a later direct probe found Ollama unreachable. After restart, two concurrent gateway requests returned HTTP 200 and Ollama remained reachable in a follow-up probe. The cause of the earlier process outage, sustained serving stability, true live peak concurrency and performance benefit remain **unestablished**. These results must not be treated as a successful load benchmark or confused with deterministic FIFO-order tests.

### Upstream failure observability — October 14, 2026

An independent [v1 aggregate upstream failure endpoint](operations/upstream-failure-metrics.md) classifies connection refusal, timeout, other transport errors, upstream HTTP rejection/status, malformed response and streaming failures. Counters are local, anonymous, reset on restart, and independent of admission counts. This is diagnostic instrumentation only: it cannot attribute the earlier Ollama process outage or establish improved uptime, reliability or inference performance.

## Engineering milestones

- **Now:** preserve a secure loopback development boundary, explicit opt-in settings, reproducible contracts, CI and concise architectural documentation.
- **Next evidence milestone:** collect model quality and controlled serving sweeps under matched power/runtime/queue conditions; complete evidence manifests; investigate drift and independent repetitions.
- **Next engineering milestone:** add measurable admission observability, controlled policy comparison and carefully specified tests; no production performance claims without representative benchmarks.
- **Later:** authentication/authorization on inference, deployed PostgreSQL operations, model revision registry, vLLM GPU backend, quotas, cost/VRAM telemetry and deployment hardening.

For planned work, see [ROADMAP.md](../ROADMAP.md). For restrictions and disclosure, see [SECURITY.md](../SECURITY.md).

## Verified user profile milestone (local, opt-in)

An authenticated `/api/v1/me` create/read/update slice now uses an operator-pinned, verified OIDC access token to choose the `(issuer, subject)` key and persists only a bounded display name and UTC timestamps to separately migrated PostgreSQL. No profile read/write is enabled without a configured verifier and trusted database. Offline and ephemeral PostgreSQL CI test identity isolation, strict inputs and fail-closed errors. No IdP-hosted login, browser session, Redis coordination, load balancer or multi-user dashboard is deployed. See [target architecture](architecture/identity-data-redis-load-balancing.md).

## Redis distributed account quota (opt-in)

The control API can enforce a shared Lua-backed 20/60-second mutation quota for verified OIDC profiles when an authenticated localhost Redis and independent HMAC secret are explicitly supplied. Offline tests cover quota exhaustion, malformed settings, identity isolation and fail-closed errors; **real Redis and multiple replicas remain untested**, and permissions are never cached in Redis. See the account runbook.

## Optional local load-balancer experiment

HAProxy localhost-only port 8090 can route approved single-turn local chat between two separately operated loopback Go gateway replicas with least-connection balancing and /health/upstream probes. The Model Manager target is operator-selectable as only 8081 or 8090. HAProxy syntax and port safety are CI-tested, but **no actual multi-replica user hardware test, streaming continuity proof, throughput improvement, production ingress or global quota is claimed**. [Operator runbook](operations/local-ha-proxy.md).

## Optional browser OIDC login (local development)

A browser-facing My Account page and OIDC Code+PKCE login/callback/logout endpoints now use operator-pinned public JWKS, one-time Redis flow state, short-lived HttpOnly same-site local cookies, JWT subject matching, CSRF-protected profile write routes and the existing PostgreSQL user profile store. There are no local passwords or stored refresh/access tokens. These endpoints remain **disabled by default**; real provider configuration, a live IdP/Redis/PostgreSQL test, production HTTPS/Secure cookie support, upstream revocation handling, organization grants and authenticated Go gateway invocation are NOT yet complete. [Runbook](operations/browser-oidc-login.md).
