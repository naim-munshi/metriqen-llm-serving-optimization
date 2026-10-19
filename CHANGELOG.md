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
  owner-only private reports. The operator later reported actual M2 native
  Ollama/Go smoke, full sequential Stop/Start/Promote/Rollback inference
  PASS, matching private evidence consistency PASS and authentic Safari
  final-state screenshots. Those are **operator-sourced observations**;
  independently published sanitized browser evidence and formal P1 sign-off
  are still pending.

### P2 prospective benchmark orchestration

- Added a guarded one-phase operator CLI (`tools.benchmark.phase_run`) and
  synthetic tests for AC/pinned-digest/configured-parallelism preflight,
  9-run real local sweep, 36-sample reference-quality evaluation, and
  separate explicit evidence finalization. It neither restarts Ollama nor
  attests actual runtime slots, Mac/GPU execution or performance gains.
  Added a separate four-phase offline reviewer to bind all timed
  preflights, matched quality and raw reports, and write guarded private
  aggregate evidence in one command. A fresh real A1/B1/A2/B2
  four-phase result is **not yet claimed**.

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

### P6 documentation consistency

- Reconciled README, portfolio reviewer briefing and the longer SaaS
  roadmap with **existing opt-in** guarded Go project/digest authorization,
  Redis-backed sessions/quotas and PostgreSQL verified-subject team
  invitations, cancellation, revocation and audit. Restored clear
  distinction between implemented code and the default unguarded local
  gateway; real IdP acceptance and production security remain unverified.
  Added a source-only documentation regression contract.

### P4 three-console keyboard access

- Added skip-to-main keyboard links with visible focus to the Model Manager
  and optional OIDC Account Console, explicit select/input focus in the
  Model Manager, reduced-motion support, and horizontal mobile Account
  navigation. Contract tests check all three consoles without asserting
  any actual Mac browser verification or enabled identity provider.

### P4 responsive operations navigation fix

- The Go Operations console previously hid the entire section nav at
  mobile widths and hid all inactive section links at tablet widths.
  It now offers visible, keyboard-focusable tablet links, scrollable
  mobile navigation with practical touch targets, and a first-focus
  skip-to-content link; health descriptions wrap on phones. A static
  Node regression test checks DOM and CSS invariants. Real Safari/Chrome
  mobile/keyboard visual acceptance remains operator-only.

### CI toolchain upkeep

- Updated GitHub CI to SHA-pinned official checkout v7.0.1,
  setup-python v7.0.0 and setup-go v7.0.0 using Dependabot-proposed
  commit digests. This is CI infrastructure maintenance, not model or
  latency performance evidence; validate all 11 jobs on current main.

### P3 FIFO policy comparison preparation

- Added strict offline `tools.benchmark.admission_tradeoff` analysis for
  real, operator-produced queue depth 0 versus 2 overload sweeps. All four
  offered concurrency levels include observed successes, HTTP 429s, goodput
  and successful-only p95/TTFT changes. It revalidates raw observations,
  rejects mismatched model/dataset/configuration, and never reports a
  proven speedup, completed P3 or answer-quality parity. No real new Mac
  FIFO A/B experiment is claimed.

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
