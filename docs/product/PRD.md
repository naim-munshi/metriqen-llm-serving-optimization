# Product requirements — working baseline

## Goal
Deploy open-weight LLMs, measure performance under concurrency, optimize serving using runtime/model settings, and report improvements with model quality controls.

## Users
AI inference engineers, MLOps/platform engineers, and administrators.

## Planned requirements
- Role-aware admin/user access, project isolation, secrets, audit logs.
- Record model revision, approval, license and runtime configuration.
- Run inference against approved serving endpoints with streaming and limits.
- Measure TTFT, throughput, p95 latency, peak VRAM, failure rate and estimated cost.
- Compare baseline to quantization, batching and caching configurations.
- Reproducible datasets/workload profiles; compare model quality at controlled settings.
- Operational monitoring, deployment verification and rollback.

## Phase 01 acceptance
Python and Go health-check services exist, tests cover success and invalid routes, CI runs both test suites, and README makes no unsupported inference or performance claims.
