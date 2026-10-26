# Metriqen Portfolio v1 — final closeout decision record

**Audit date:** 2026-10-27 JST. **Verified source baseline:**
[`61d62ce577826904b4d83991793ee6a4873cd91d`](https://github.com/naim-munshi/metriqen-llm-serving-optimization/commit/61d62ce577826904b4d83991793ee6a4873cd91d);
[post-merge GitHub CI: 12/12 successful](https://github.com/naim-munshi/metriqen-llm-serving-optimization/actions/runs/40159749284).
This is the last independently verified source baseline at the time of this
audit, **not** a claim of final Mac acceptance. Later commits require a
new CI verification before release.

**Decision: NO-GO for a `v1.0.0` release** until all six independent
[Portfolio v1 acceptance gates](../PORTFOLIO_V1_ACCEPTANCE.md) are
approved with authentic evidence. **Do not close
[release tracker #81](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81),
tick its boxes, create the tag, publish a GitHub release or describe this
as a production SaaS** just because hosted tests are green. An owner may
choose to stop developing and present the accurately labelled research
prototype; that is different from a verified v1.0 release.

## What GitHub can finish without the operator Mac

- [x] Go HTTP gateway, bounded local admission and JSON/SSE validation,
  cancellation/recovery tests and local error-mapping code in source.
- [x] Optional local FastAPI model/deployment management with pinned-digest
  approval, model load, Stop/Start/Promote/Rollback, SQLite transaction
  cleanup and concurrent state protections.
- [x] Versioned workloads, quality reference checks, ABAB/overload evidence
  reviewers, benchmark integrity checks and explicit negative findings.
- [x] Hosted 12-job CI including synthetic Go/Node/Python contracts,
  Go race/vet and ephemeral PostgreSQL/Redis integrations. This is not
  a physical Apple GPU/Metal or IdP acceptance test.
- [x] Source-controlled architecture, security boundaries, reviewer
  walkthrough, runbooks, change history and release acceptance rules.

The historical original `assistant` local lifecycle was operator-reported
successful. Its **existing rollback history is protected**. On the last
separately observed Mac test the new `acceptance-test` alias was absent;
the isolated alias setup tool has only run in synthetic CI, not on the Mac.
Do not mutate the existing `assistant` alias to work around this.

## Remaining independent evidence and exact owner decisions

| Gate | Existing evidence (not enough to close) | Necessary final evidence / decision |
| --- | --- | --- |
| **P1** native and deployment | Operator-reported real Ollama/Go JSON/SSE smoke and earlier full `assistant` lifecycle; Safari candidate real inference; private cross-report check | Revalidate after latest merged source; inspect actual processes, pinned digest and original results. Complete a reviewed isolated lifecycle if retesting; capture approved, original browser evidence. No false GPU attestation |
| **P2** controlled measurements | Oct 20, 36 timed runs / 1,296 successful measured requests; internal raw-report consistency PASS | Independent review of B1 benchmark-time model slots, A2 continuous AC, thermals and large within-setting drift; rerun A1/B1/A2/B2 if controls cannot be established; preserve contrary results |
| **P3** attributable optimization | Oct 21 FIFO depth 0 vs 2: 144→252 completed of 432 offered, but worse successful goodput at **all** tested concurrency levels and overloaded TTFT/p95 regressions | Profile a **Metriqen-owned** serving bottleneck and prospectively benchmark one PR/change with identical pinned model/decoding/workload and quality checks. A neutral/negative result is valid evidence but **not** the contract's claimed speedup. If the owner changes the portfolio hypothesis to a verified reliability/latency tradeoff, revise the acceptance contract transparently before any sign-off |
| **P4** real reviewer demo | Three separate local consoles, source-level CSS/DOM and keyboard tests, operator-supplied desktop Safari snapshots | Operator visually tests genuine Safari/Chrome desktop and 320/390px mobile widths, keyboard/VoiceOver, error/recovery, and approves redacted, authentic screenshots + a 2–3 minute local demo video. Never use synthetic mock screenshots as live proof |
| **P5** recovery, identity and isolation | Go race tests, Redis/Postgres ephemeral CI, synthetic auth/recovery/SSE and quotas | On actual configured local environment, test Ollama outage/restart, streaming interruption/cancel, release of admission, reload behavior, and enabled opt-in OIDC/JWT/Redis/PG isolation. Explicitly mark absent identity/backend configurations **not tested**; localhost only |
| **P6** repo release | README, CHANGELOG, case study, release acceptance spec and green source CI | Repository owner selects a permissible license **or** documents an explicit proprietary decision; enables/reviews real `main` branch rules; approves redacted public media and reports; final CI on release commit; signs off P1–P6; **then** creates `v1.0.0` and release notes |

### Smallest safe operator route (when at the Mac)

1. Review `git status --short`; preserve local modifications, then
   `git pull --ff-only origin main`, record `git rev-parse HEAD` and
   versions. Keep Ollama, Gateway and Model Manager **only on loopback**.
2. Run `make portfolio-ci` in the active development environment,
   then `python3 -m tools.demo.portfolio_readiness`. The latter
   **deliberately exits 2 / BLOCKED**, even with green source CI.
3. With original local Ollama and Go services running, execute the
   [real pinned-smoke protocol](real-ollama-portfolio-acceptance.md).
   Do **not** invent an expected digest or publish `reports/local/`.
4. For a fresh isolated alias, first run
   `python3 -m tools.demo.isolated_deployment_setup`
   (read-only). If absent, and only when the Mac is idle and the
   operator permits memory/model state changes, use the documented
   `--execute --load-baseline` setup. The baseline load can evict
   another Ollama resident model. **Never modify `assistant`'s rollback
   history to make a test pass.**
5. Run read-only
   `python3 -m tools.demo.local_deployment_acceptance --name acceptance-test --baseline llama3.2:1b --candidate llama3.2:latest --sequential-load`.
   Before opt-in `--execute`, independently verify the test alias has no
   previous revision, the Gateway actually allows both model tags and
   there are no other test users. Record genuine response and rollback
   evidence. This script changes local routing when execution is enabled;
   a synthetic PASS cannot replace actual operator observation.
6. Follow the [P2 A/B/A/B protocol](../benchmarks/abab-replication.md)
   and [P3 serving-path bottleneck tracker](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/123);
   inspect both negative and positive deltas with confidence/drift.
   Use [final Mac handoff](portfolio-final-mac-handoff.md) for P4/P5.
7. Personally review/report only sanitized aggregate evidence, real media
   and precise limitations. Keep **token, raw prompts, private screenshots,
   database state, JWT, DSN and full private benchmark artifacts** out
   of GitHub issues, commits and any public release.

## Owner sign-off record (all remain unapproved)

This is a **template for human review**, not a checked or accepted ledger.
When a gate is actually reviewed, record in GitHub tracker #81: a direct
sanitized evidence link, date/time, the exact code SHA, a reviewer decision,
and limits. Do not upload secrets or fake observations.

- [ ] P1 — real model and integrated local UI lifecycle
- [ ] P2 — independently reviewed controlled repeated benchmark
- [ ] P3 — Metriqen-owned measured change and honestly bounded finding
- [ ] P4 — genuine desktop/mobile/keyboard walkthrough and approved media
- [ ] P5 — configured security/identity and recovery observations
- [ ] P6 — branch rules/license/public evidence/final CI/release approval

Repository **`main` reported `protected=false`** in the GitHub read-only
audit on October 27. A source file, issue comment or green check does not
enable GitHub branch protection; use the
[owner/admin ruleset procedure](repository-branch-hygiene.md). The owner's
license choice has **not** been made and no `v1.0.0` release exists as
of this audit. **No production public exposure, paid GPU hosting, Google
login or autoscaling is part of the v1 closeout.** Keep optional v2
[#114](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/114),
[#115](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/115) and
[#116](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/116)
separate, not silently counted toward v1 completion.
