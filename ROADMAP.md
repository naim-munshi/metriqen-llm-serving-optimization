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

## Completed proof of concept

A local-only Go streaming inference bridge with request validation, cancellation and mocked integration tests is implemented independently of the future public serving gateway. **Real Ollama or vLLM model execution has not been tested in this milestone**; GPU-serving, benchmarks and tenant-level controls remain on the roadmap.

Follow one reviewable change per PR; do not manufacture a commit history.
