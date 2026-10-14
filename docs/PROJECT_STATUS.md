# Metriqen — implementation and evidence status

**As of October 14, 2026.** This is a local engineering prototype, not a production LLM deployment. Current statements distinguish implemented code, test coverage, operator observations, and planned components.

## Scope matrix

| Component | What exists | Verification | Not established |
| --- | --- | --- | --- |
| Go inference gateway | OpenAI-compatible local chat relay, SSE, input/output limits, timeout, cancellation, four active admissions | Unit tests, race-enabled CI, synthetic loopback E2E, developer-reported local Ollama test | Authenticated public endpoint or GPU serving |
| Request admission | Opt-in FIFO queue (0–32 pending, 5-second wait cap), default immediate 429, per-process cancellation and backpressure | Go tests with race detector, HTTP fixtures | Batching, fairness between tenants, speedup, GPU memory optimization |
| Model allowlist | Optional strict model **tag** filtering before upstream | Negative/configuration tests | Immutable approved model digests |
| Benchmark runner | Versioned workload, rotating concurrency 1/2/4 repeated sweeps, TTFT, p50/p95, success rates, token usage, optional UTC run timing | Python unit tests and private operator-run measurements | Cloud/GPU-attested measurements, broad workload representativeness |
| Evaluator | 36-question starter quality workload, exact and normalized match | Synthetic CI tests, separately reported local model results | Semantic/factuality/hallucination or safety grading |
| Experiment analysis | Pairwise comparison; A/B/A and A/B/A/B summaries; consistency/evidence gates; local review reports | Python CI fixtures, human-attested local raw reports | Causal attribution or reliable population-p95 uncertainty intervals |
| FastAPI control service | Health, fail-closed pinned JWKS access-token identity, role-scoped project-read seams | Python tests with mocked/synthetic identities | Production login, full tenant CRUD or authenticated Go inference |
| Relational memberships | PostgreSQL-targeted schema and optional SQLAlchemy adapter | SQLite-backed tests | Live PostgreSQL-backed CI, automatic schema migration, production tenant isolation |
| Docker Compose | Loopback host mappings and local health-only services | Configuration committed | Complete local Docker validation across machines |
| Production platform | Roadmap only | — | vLLM, GPU batching, cost-aware scheduling, observability, HA, multi-tenant APIs, web console |

## Research evidence separation

### Published, operator-attested case study — October 13, 2026

[Parallelism 1 vs 2](benchmarks/experiments/2026-10-13-ollama-parallelism.md) describes two sequential Ollama 0.30.8 configurations with a pinned `llama3.2:1b` revision on an Apple M2 (8 GiB, Metal), 648 successful measured requests total, and 36 reference questions per configuration. At offered client concurrency 2, the observed change in requests/s was **+34.235%**. At concurrency 1, requests/s worsened **−3.895%**. These are descriptive observations of **Ollama's configured parallelism**, not the gateway's new FIFO queue.

CI validates public transcription arithmetic and synthetic report contracts, not the private raw inference measurements. No causal or general optimization claim is justified.

### Unpublished operator-local follow-ups

Additional Oct 13 A2 and Oct 14 A1 sweeps, variability checks, and a separate battery-powered 36-question quality evaluation are reported by the developer. They were not consolidated into a controlled, public four-phase A/B/A/B dataset, and the October 14 battery quality run must **not** be presented as a matched AC-controlled experiment. Reports remain Git-ignored under `reports/local/`.

No new public benchmark figure should be added unless its provenance and limitations are independently checked against source reports.

## Engineering milestones

- **Now:** preserve a secure loopback development boundary, explicit opt-in settings, reproducible contracts, CI and concise architectural documentation.
- **Next evidence milestone:** collect model quality and controlled serving sweeps under matched power/runtime/queue conditions; complete evidence manifests; investigate drift and independent repetitions.
- **Next engineering milestone:** add measurable admission observability, controlled policy comparison and carefully specified tests; no production performance claims without representative benchmarks.
- **Later:** authentication/authorization on inference, live PostgreSQL testing, model revision registry, vLLM GPU backend, quotas, cost/VRAM telemetry and deployment hardening.

For planned work, see [ROADMAP.md](../ROADMAP.md). For restrictions and disclosure, see [SECURITY.md](../SECURITY.md).
