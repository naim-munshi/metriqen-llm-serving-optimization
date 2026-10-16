# Delivery roadmap

Each item is a milestone; planned work must not appear in the README as implemented.

| Phase | Scope | Verification |
| --- | --- | --- |
| 01 | Health-check services, monorepo foundation, CI | Local tests; remote CI after PR |
| 02 | Identity boundary, authorization policy contract, initial threat model | Policy tests, security design review; runtime enforcement follows in Phase 03 |
| 03 | OIDC login, organizations, RBAC, API keys | Authorization and tenant-isolation tests |
| 04 | Model registry and immutable approved revisions | Validation tests |
| 05 | vLLM GPU serving and baseline inference | Real reproducible GPU measurements |
| 06 | Production inference proxy with authentication, streaming and per-project quotas | Authenticated concurrent traffic and failure tests |
| 07 | Benchmark runner and experiment metadata | Reproducible controlled load tests |
| 08 | Quantization/batching/cache optimization | Before/after result at comparable quality |
| 09 | Telemetry, GPU observability, cost calculation | Verified metrics and cost assumptions |
| 10 | Secure web console and deployment lifecycle | End-to-end role-aware tests |
| 11 | Operational hardening and portfolio release | Security, recovery, performance and demo evidence |

## Opt-in local model workflow (functional development slice)

A local-only control API can list installed Ollama models, persist canonical tags with verified SHA-256 digests in a private SQLite registry, approve revisions and issue model **preload/unload** through native Ollama APIs and run a bounded, approval-checked **single-turn chat** through the fixed local Go gateway. This is not a managed deployment: no new model download, no long-running worker lifecycle, no per-project model authorization and no Go gateway enforcement. It is disabled by default and guarded by a distinct loopback operator token. See [local model registry](docs/operations/local-model-registry.md). The remaining model registry, secure multi-user deployment and optimization phases are unchanged.

## Local tooling and operator-reported performance evidence

A dependency-free Python benchmark CLI with offline tests now records warm-up-excluded TTFT, p50/p95 latency, successes, failures, request throughput and optional token usage for a loopback inference endpoint. A developer has now executed pinned-model runs and locally validated their report consistency; see the [public aggregate experiment](docs/benchmarks/experiments/2026-10-13-ollama-parallelism.md). GitHub CI still checks synthetic fixtures only; there is no independent hardware attestation or vLLM/GPU measurement.

## Completed proof of concept

A local-only Go streaming inference bridge with request validation, cancellation and mocked integration tests is implemented independently of a future public serving gateway. A developer has subsequently verified real Ollama 0.30.8 generation on Apple M2 and completed two separate nine-run 36-request-per-run sweeps with strict, operator-attested evidence consistency checks and a separate reference-quality comparison. **Only sanitized aggregates are public; the raw evidence is private and public CI cannot independently reproduce the measurements**. See the [2026-10-13 parallelism experiment](docs/benchmarks/experiments/2026-10-13-ollama-parallelism.md). vLLM GPU serving and production gateway controls remain planned.

A PostgreSQL-targeted tenant schema and read-only SQLAlchemy adapter have offline SQLite-backed integration tests and a separate **real PostgreSQL 16 GitHub Actions integration job** that applies the checked-in SQL migration to an isolated disposable database, then checks composite foreign keys, access controls and revocation. This is **not** a deployed or production-hardened membership store. An operator may opt in locally with an explicit loopback PostgreSQL configuration; no database is wired into default runtime. Live identity-provider authentication, production migration automation and production RBAC remain planned. See [PostgreSQL CI evidence](docs/operations/postgres-ci.md).

Follow one reviewable change per PR; do not manufacture a commit history.

## Serving comparison readiness

The offline comparator can revalidate two local evidence bundles and report p50/p95, TTFT and throughput deltas with a separately checked exact-match quality gate. **A passing comparison is descriptive, not a causal optimization result.** The legacy example workloads contain just three performance prompts and two reference questions; new **versioned v1 starter datasets** contain 36 technical prompts and 36 deterministic-answer references, with offline schema checks. Both remain synthetic and potentially unrepresentative; the first developer-run comparison is documented as `descriptive_not_causal`, and no general/causal speedup claim is eligible until independently replicated experiments, representative workloads and uncertainty analyses exist. See [controlled comparison protocol](docs/benchmarks/controlled-comparison.md).

## A/B/A study readiness

The existing A1/B parallelism observations are documented, and the developer now reports an A2 return-to-one-slot nine-run sweep and private A/B/A analysis (`descriptive_reversion_observations_only`). The third-phase numeric table and thermal/context notes have not been independently examined or published; **this does not establish recovered performance or a causal effect**. The new local-only reviewer CLI can render the validated three-phase comparison for inspection. An entirely new prospectively planned uninterrupted A/B/A session remains a stronger future experiment. See [the A/B/A analysis guide](docs/benchmarks/aba-analysis.md) and [reversion protocol](docs/benchmarks/aba-reversion-protocol.md).

## NLP quality-evaluation track

- Baseline: deterministic ID-aligned exact and normalized exact match, versioned dataset fingerprints and aggregate-only reports.
- Completed locally on 2026-10-13: the developer obtained 36 reference responses per setting; exact match was 31/36 and normalized match 33/36 for both parallelism 1 and 2. Raw outputs were not published.
- Next: independently inspect response quality under stronger datasets; add semantic evaluation only with validated references and calibration.
- Gate: do not claim quantization or batching improves performance at equivalent quality until benchmark and NLP evaluation evidence is paired.

NLP quality measurement is not the same as runtime inference performance and must be reported separately.

## Account authentication and scalable data platform

- **Implemented (opt-in):** verified OIDC user-profile create/read/update through PostgreSQL; issuer-scoped identity and negative authorization tests. No user signup, login/logout or session issuance yet.
- **Next functional gate:** an operator-selected OIDC provider with Authorization Code+PKCE, session cookie/CSRF controls, proper account UI and PostgreSQL-backed tenant CRUD.
- **Next distributed-systems gate:** authenticated Redis for expiring session state and atomic per-identity quotas; protected load-balanced Go inference with SSE/health-aware routing. Neither Redis nor LB is claimed as deployed. [Security architecture](docs/architecture/identity-data-redis-load-balancing.md).

**Implemented opt-in Redis increment:** Account POST/PATCH mutation quotas can use a bounded atomic Lua counter in an independently authenticated local Redis server with HMAC-obfuscated keys and fail-closed outage behavior. No Redis is deployed by default; Redis-backed BFF sessions, auth-entrance protection and distributed gateway quotas remain future milestones.

**Implemented opt-in LB configuration:** HAProxy local-only least-connection routing across Go gateways on 8081/8082 with an 8090-only proxy listener, fixed-path POST admission and upstream API reachability checks. CI validates HAProxy syntax. This does not prove increased inference speed or provide production tenant-level balancing/security. [Runbook](docs/operations/local-ha-proxy.md).

**Opt-in browser login increment:** The FastAPI control plane now supports OIDC Code+PKCE redirect/callback, pinned token/nonce verification, Redis one-time state and 30-minute sessions, CSRF-guarded profile CRUD, logout, and an interactive local My Account page. This **requires a compatible operator-configured external IdP and authenticated local Redis/PostgreSQL**; no live IdP connection or production TLS/Secure cookie acceptance test is claimed. [Local login runbook](docs/operations/browser-oidc-login.md). Next: hosted IdP acceptance, secure production BFF, org/project membership CRUD, access-governed gateway and audit trails.
