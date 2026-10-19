# Changelog

Metriqen is an actively developed **local research and portfolio
prototype**. This file records reviewed engineering changes while keeping
unreleased features separate from finished portfolio acceptance evidence.

There is **no tagged Portfolio v1.0 release yet**. Do not interpret this
document as a claim of commercial SaaS readiness, trusted token billing,
independent hardware attestation or a production security audit.

## Unreleased — Portfolio v1.0 preparation (2026-10)

### P1 deployment acceptance preparation

- Added a read-only-by-default, explicit-`--execute` local operator
  acceptance CLI and synthetic tests for real alias Stop → denied chat
  → Start → chat → Promote → chat → Rollback → chat. It uses only fixed
  loopback APIs, private operator-token file, controlled recovery and
  owner-only private reports. This is **not** a real Mac run or an
  independently verified browser workflow; P1 remains unchecked.

### Operator demo and reviewer workflow

- [#82](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/82):
  new responsive provider-reported aggregate token-usage panel in the
  loopback Go operations console; fixed-link three-console reviewer
  navigation between the Go data plane, FastAPI model manager and
  optional OIDC account screen. **Separate security principals remain
  separate; the dashboard does not log in to those services.**
- [#80](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/80):
  added an operator-driven, three-request real Ollama + Go gateway
  acceptance command validating model digest, nonstream, SSE and
  gateway metrics, with private no-overwrite reports. Hosted CI uses
  **fake HTTP fixtures only**, not the owner's Mac/GPU.
- [#79](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/79):
  strict offline A/B/A/B phase-to-preflight evidence binding, requiring
  four timed phases, fresh local preflights and pinned operator-reported
  configuration. It does not attest real runtime slots.

### Inference integrity and telemetry

- [#78](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/78):
  bounded provider SSE usage self-report observation, incomplete or
  duplicate/invalid event classification and null count regression fix.
- [#77](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/77):
  process-local anonymous aggregate provider-reported
  nonstream usage diagnostics.
- [#76](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/76):
  conservative requested maximum output-token **reservations** per
  project, with replay defense; not measured model usage or billing.
- [#75](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/75):
  opt-in Redis 7 cross-replica active-inference lease limits and
  local authorization integration.

### Deployment control and benchmarking

- [#74](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/74):
  reduced gateway JSON model-selection overhead in a **synthetic Go
  CPU microbenchmark**. No real LLM inference performance speedup
  or GPU optimization was established.
- [#73](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/73):
  opt-in deployment-bound model-routing authorization and loaded
  model digest comparison.
- [#72](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/72):
  local, operator-only deployment reconciler.
- [#71](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/71):
  durable PostgreSQL desired deployment state and versioned intent.
- [#70](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/70):
  optional Redis project request-rate quota.
- [#69](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/69):
  optional signed JWT + current project SQL authorization seam.

### Portfolio evidence

- [October 13 local Ollama configuration study](docs/benchmarks/experiments/2026-10-13-ollama-parallelism.md):
  operator-attested descriptive result, **not causal**.
- [October 18 gateway CPU microbenchmark](docs/benchmarks/experiments/2026-10-18-model-selection-cpu.md):
  synthetic Go parsing performance, **not model serving throughput**.
- [Portfolio case study](docs/PORTFOLIO_CASE_STUDY.md) and
  [P1–P6 release acceptance](docs/PORTFOLIO_V1_ACCEPTANCE.md):
  reviewer-facing summary and objective release blockers.

## Pending before Portfolio v1.0

- [ ] Operator-run real Mac/Ollama model-management, chat, streaming,
      digest and rollback evidence.
- [ ] Prospective A/B/A/B raw model serving/quality results with honest
      before/after attribution and regressions.
- [ ] Real browser walkthrough, visual/keyboard QA and sanitized
      genuine screenshot/video assets.
- [ ] Complete security/operational acceptance on the operator machine.
- [ ] Repository owner explicitly chooses the intended open-source
      license (or decides to retain proprietary rights); do not
      silently add an arbitrary license.
- [ ] Final release commit has passing CI and independently reviewed
      acceptance evidence; only then tag `v1.0.0`.

Track the evidence in [Portfolio v1.0 issue #81](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81).
