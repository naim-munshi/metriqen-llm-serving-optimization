# Changelog

Metriqen is an actively developed **local research and portfolio
prototype**. This file records reviewed engineering changes while keeping
unreleased features separate from finished portfolio acceptance evidence.

There is **no tagged Portfolio v1.0 release yet**. Do not interpret this
document as a claim of commercial SaaS readiness, trusted token billing,
independent hardware attestation or a production security audit.

## October 23 — prospective AC-powered Go SSE A/B/A/B comparison

- Added `tools.benchmark.sse_abab` for **operator-initiated**, sequential
  `A1 (1 KiB) → B1 (4 KiB) → A2 (1 KiB) → B2 (4 KiB)` real local Go
  gateway diagnostics on the same endpoint. Default is plan-only; each
  `collect --execute` phase verifies AC, exact installed Ollama
  digest, gateway dependency health and observed process/buffer mode
  at boundaries. It never restarts servers, reads credentials or
  selects a different inference URL. An offline four-phase report
  retains full failure counts and per-concurrency descriptive latency,
  TTFT and throughput without claiming speedup.
- [Owner Mac procedure](docs/benchmarks/sse-abab-ac-operator.md),
  synthetic negative-path tests and strict independent manual
  power/thermal/slot/model-quality limitations. No actual Mac phase,
  reproducible performance gain or P3 sign-off was performed in CI.

## October 23 — no-AC-power local reviewer and security preflight

- Added an explicit-opt-in, read-only
  [local service preflight](docs/operations/local-readonly-review-preflight.md)
  for the already-running Ollama, Go and FastAPI localhost endpoints,
  including model manager anonymous **401** defense-in-depth.
  It defaults to an offline plan, sends only fixed bounded GETs after
  `--execute`, rejects redirects and proxy use, and can save private
  sanitized summaries without credentials or response bodies.
- This command **never runs a model**, browses a page, logs into Google,
  restarts servers, attests loopback-only listeners or approves P1/P4/P5.
  Offline synthetic and privacy/adversarial regression tests are included.

## P3 prospective path-overhead diagnosis (operator-only, unreleased)

- Added an explicit `--execute` **native Ollama vs Go gateway** ABBA
  diagnostic for the same pinned local model and 36-prompt workload,
  concurrency 1/2/4. It checks installed digest, AC power, endpoint
  reachability and private report paths, retaining failures and
  success-only TTFT/p95/RPS for every phase. With no `--execute`, the
  command never accesses the network or creates files.
- The diagnostic measures **overhead and attribution hypotheses**, not
  a changed Metriqen serving path, model speedup, GPU optimization,
  controlled semantic quality comparison or P3 acceptance. Its owner
  must independently verify gateway upstream, runtime slots, power and
  thermal conditions. [Protocol](docs/benchmarks/gateway-overhead-diagnostic.md).

## October 23 — current evidence and safe release-audit maintenance

- Added [current evidence and limitations matrix](docs/PORTFOLIO_CURRENT_STATUS.md)
  separating verified code, operator-reported Mac observations,
  green source CI, negative optimization results, and unverified
  production/Google/GPU claims. Historical `docs/PROJECT_STATUS.md`
  remains explicitly archived, not a current feature inventory.
- Added an offline, read-only
  [six-gate release readiness audit](docs/operations/portfolio-release-readiness.md)
  with JSON output and deliberate blocked exit status 2, even when
  source docs or a license file exist. It cannot forge real Mac/browser
  evidence or tag a release.
- Recorded recent opt-in v2 **source code only**: local [Google
  OIDC compatibility #113](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/113),
  [Google public JWKS refresh #119](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/119),
  [GPU worker count recommendation #117](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/117),
  and [offline GPU fleet reconciliation #120](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/120).
  **No live Google login, public HTTPS or GPU scaling deployed.**
- Current verified reference main CI: [11/11 jobs on
  `c772963`](https://github.com/naim-munshi/metriqen-llm-serving-optimization/actions/runs/39883279798).
  Operator's last supplied Mac `make portfolio-ci` transcript verified
  an earlier `243bd1f` commit with 916 explicitly passing source
  tests plus Go race/vet; it was not a browser/GPU/real IdP test.

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
  aggregate evidence in one command. The operator subsequently reported
  a complete October 20 A1/B1/A2/B2 study and private evidence-consistency
  PASS. The [sanitized reviewer report](docs/benchmarks/experiments/2026-10-20-ollama-parallelism-abab.md)
  records 1,296/1,296 successes, stable narrow reference quality, **large
  same-setting drift and non-replicated candidate effects**. B1-time slots
  and continuous A2 AC are not independently documented; this does not
  close P2 or demonstrate a causal performance improvement.

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

### P3 FIFO policy comparison and observed admission tradeoffs

- Added guarded **operator-only** P3 A/B collector: Mac AC/model/digest/process-config preflight, live gateway queue-capacity checks, 12-run overload sweep per phase, 36-answer quality evaluation and postflight. It never changes processes, authenticates GPU/runtime slots, or asserts a speedup.
- Added strict offline `tools.benchmark.admission_tradeoff` review of all measured request outcomes and HTTP 429s, goodput, successful-only p95 latency/TTFT and gateway admission counters. It rejects mismatched experimental configurations; does not automatically approve P3.
- **October 21 operator-observed FIFO study:** queue depth 0 vs 2, unchanged Ollama configured parallelism 2, 864 total measured requests, 72 separate reference-quality completions; private collector and offline evidence-consistency checks PASS. [Sanitized descriptive aggregate](docs/benchmarks/experiments/2026-10-21-fifo-admission-overload.md) shows completed admissions increased **144→252 of 432** and observed 429s decreased **288→180**, while **goodput regressed at all concurrency levels**, and overloaded successful-only p95/TTFT generally increased. Narrow quality scores matched. Uninterrupted power/thermals, independent runtime attestation, operator privacy approval and formal P3 sign-off remain open. **No Metriqen inference-speedup claim.**

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

- [ ] Formal acceptance and privacy-reviewed public evidence of the
      operator-reported successful Mac/Ollama smoke and full lifecycle;
      the real operator executions have already been reported.
- [ ] Human acceptance of the newly collected October 20 A/B/A/B result,
      including independently reviewed slot/power caveats and drift. The
      private consistency PASS and published aggregate do not by themselves
      close P2.
- [ ] Real browser walkthrough, visual/keyboard QA and sanitized
      genuine screenshot/video assets.
- [ ] Complete security/operational acceptance on the operator machine.
- [ ] Repository owner explicitly chooses the intended open-source
      license (or decides to retain proprietary rights); do not
      silently add an arbitrary license.
- [ ] Final release commit has passing CI and independently reviewed
      acceptance evidence; only then tag `v1.0.0`.

Track the evidence in [Portfolio v1.0 issue #81](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81).
