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

## Verified local tooling (not model performance)

A dependency-free Python benchmark CLI with offline tests now records warm-up-excluded TTFT, p50/p95 latency, successes, failures, request throughput and optional token usage for a loopback inference endpoint. Its measurements are *not* real until executed against a pinned model and documented hardware; vLLM/GPU testing remains required.

## Completed proof of concept

A local-only Go streaming inference bridge with request validation, cancellation and mocked integration tests is implemented independently of a future public serving gateway. A developer has subsequently verified real Ollama 0.30.8 generation on Apple M2 and provided preliminary six-request local benchmark outputs, including a pinned-digest run with streamed token counts. **The repository contains no published reproducible nine-run comparison or vLLM GPU result**; production gateway controls remain planned.

A PostgreSQL-targeted tenant schema and read-only SQLAlchemy adapter have offline SQLite-backed integration tests, including composite foreign-key tenant boundaries. This is **not** a deployed or PostgreSQL-tested membership store. An operator may opt in locally with an explicit loopback PostgreSQL configuration; no database is wired into default runtime. Live identity-provider authentication, migrations execution and production RBAC remain planned.

Follow one reviewable change per PR; do not manufacture a commit history.

## Serving comparison readiness

The offline comparator can revalidate two local evidence bundles and report p50/p95, TTFT and throughput deltas with a separately checked exact-match quality gate. **A passing comparison is descriptive, not a causal optimization result.** The legacy example workloads contain just three performance prompts and two reference questions; new **versioned v1 starter datasets** contain 36 technical prompts and 36 deterministic-answer references, with offline schema checks. Both remain synthetic and potentially unrepresentative; publish no general speedup claim until adequately sized real experiments and uncertainty analyses exist. See [controlled comparison protocol](docs/benchmarks/controlled-comparison.md).

## NLP quality-evaluation track

- Baseline: deterministic ID-aligned exact and normalized exact match, versioned dataset fingerprints and aggregate-only reports.
- Next: compare real-model outputs under controlled prompt and generation settings; add semantic evaluation only with validated datasets and calibration.
- Gate: do not claim quantization or batching improves performance at equivalent quality until benchmark and NLP evaluation evidence is paired.

NLP quality measurement is not the same as runtime inference performance and must be reported separately.
