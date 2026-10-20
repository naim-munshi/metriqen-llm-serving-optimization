# Metriqen Portfolio v1.0 — scope and release acceptance contract

**Project category:** LLM Infrastructure / AI Platform Engineering portfolio.
**Purpose:** prove the author can design, implement, measure, test and clearly
explain a real, bounded open-weight model-serving system. This is **not** a
promise to ship a public SaaS, a commercial multi-tenant provider, or a new
inference kernel. This document is the single portfolio-v1 finish line.

## One-sentence engineering story

> I built a Go-based local LLM inference gateway with bounded scheduling,
> streaming and failure controls; a Python/FastAPI management plane; and
> repeatable, quality-aware serving experiments. I can demonstrate actual
> Ollama inference and explain independently validated engineering changes
> without mistaking synthetic CI results for real GPU performance.

Any claim about a speedup must identify precisely **which component or
configuration changed**. Setting `OLLAMA_NUM_PARALLEL=2` is an Ollama
configuration experiment, not a new scheduling algorithm written by Metriqen.
The repo's Linux hosted runner JSON-selection CPU microbenchmark is not a
real Ollama/GPU serving improvement.

**P1 lifecycle helper (optional):** the [explicit local deployment acceptance CLI](operations/local-deployment-acceptance.md) can test real Stop/Start/Promote/Rollback with a read-only default. Synthetic CI of its fake API fixture is not Mac/Ollama proof; use human-reviewed private operator results and real browser QA to satisfy P1.

**Cross-report P1 review (optional):** [private P1 evidence consistency reviewer](operations/p1-evidence-review.md) checks that real native JSON/SSE smoke and full deployment Promote/Rollback observations share the same pinned original model digest and contain all mandatory assertions. It never authenticates the operator or replaces browser screenshots; P1 stays unchecked until reviewed.

**Operator handoff (Mac only):** The single ordered, no-skips checklist for
real-model inference, protected deployments, A/B/A/B evidence, security
recovery, original browser screenshots, license approval and the final
release is [Portfolio final Mac handoff](operations/portfolio-final-mac-handoff.md).
The CI/source-only `make portfolio-ci` gate does not satisfy this list.
For P2, the [guarded per-phase operator collector](benchmarks/phase-runner.md)
reduces repeat commands but does not configure Ollama slots or replace four
prospective operator-observed phases and strict binding.

## Required release gates (all must pass)

| Gate | Acceptance evidence | Current known boundary |
| --- | --- | --- |
| **P1. Real-model inference and integrated operator path** | Operator-run real Ollama native chat, Go nonstream and streaming chat, installed/loaded pinned model digest, verified terminal SSE marker, gateway counters, and independent visual/browser approval/load/inference/stop/rollback demonstration | Actual M2 Ollama/Go smoke, local Stop/Start/Promote/Rollback, and pinned digest consistency have been operator-observed; authentic Safari final-state screenshots were provided in conversation, but not yet approved/published as release evidence or visually reviewed for mobile/keyboard; GitHub CI cannot attest Mac execution |
| **P2. Reproducible before/after experimental evidence** | A1/B1/A2/B2 real-model repeats with timestamped phase preflights, AC/thermal/slot notes, pinned model digest, same workload/decoding, TTFT/RPS/p95/token throughput/failures and separate quality scores; independent consistency gates pass | Operator reported Oct 20 A1/B1/A2/B2 local execution (36 timed runs, 1,296/1,296 successes, 144 quality completions) and strict private consistency PASS; new [public sanitized aggregate](benchmarks/experiments/2026-10-20-ollama-parallelism-abab.md) shows substantial drift and reversed effects. B1 benchmark-time slots and A2 uninterrupted AC lack independent confirmation; full human P2 acceptance remains open |
| **P3. At least one engineering optimization with precise attribution** | Before/after controlled comparison of an identifiable **Metriqen-controlled** code/serving path with tests, same model quality, explicit regressions and reproducible reports; microbenchmark must not be marketed as LLM speedup | Existing Go JSON-selection single-pass CPU microbenchmark has isolated synthetic wins; real end-to-end improvement still unverified |
| **P4. Coherent, usable reviewer demonstration** | One documented start/check path, simple model approval → invoke → metrics → rollback journey; browser screenshots from actual running UI, basic mobile/desktop and accessible keyboard checks; an offline mock fallback requiring no model download | Three local UIs and offline smoke exist; desktop Safari screenshots were operator-provided for Go Operations, Model Manager and final local routing state; mobile, keyboard, full UI click-transition and privacy-approved public demo evidence remain pending. Source-level CSS/DOM regressions for hidden mobile/tab navigation and missing skip-link are fixed/tested, but have not been visually attested on a Mac |
| **P5. Engineering reliability and security acceptance** | Go race/vet, Python, actual ephemeral PG and Redis integration, SSE failure, cancellation, rate/lease limits, strict tenant opt-in negative tests all pass; verify on real operator configuration without exposing localhost services | 11/11 CI successful at reference commit; optional auth/quotas cannot be presumed enabled in a default developer environment; never expose services publicly |
| **P6. Professional portfolio release** | Honest concise README with architecture, installation, feature/status matrix, reproducible public aggregate performance figures, caveats, license decision, versioned CHANGELOG, screenshot/video walkthrough, interview Q&A and tagged v1.0.0 release | README/PORTFOLIO/PROJECT_STATUS contain outdated descriptions of newer features; no verified screenshots, video, chosen license or published release |

**Definition of done:** all P1–P6 acceptance evidence is linked from
`README.md`; demo commands actually executed on the operator's Mac,
reviewed and documented; every automated CI check green on the release
commit. A green synthetic test alone **never marks P1–P3 complete**.

## Engineering milestones and ownership

1. **Integrate and demonstrate the local real model.** Build a bounded,
   human-operated smoke command that checks both native Ollama and Go
   gateway requests without restarting models/processes, reports only
   aggregate evidence, and fails on invalid digest/missing SSE completion.
   Then *the operator on Mac* must execute it and keep private evidence
   under `reports/local/`. This repo's CI can test this command only with
   synthetic transport fixtures.
2. **Run controlled A/B/A/B measurements.** Use the checked-in phase
   preflight binder and raw evidence/quality gates. Never manufacture
   a real run by editing a synthetic report. Note runtime slots and power.
3. **Make a real, attributable serving-path change.** Select one of
   queue-admission behavior, Go request-path cost, caching policy,
   batching runtime configuration, or quantization; define comparison
   target before measuring and include quality/failure regressions.
4. **Consolidate the existing UI without compromising security.** The
   operator console and verified-user OIDC paths are separate security
   roles; do **not** bypass project RBAC to offer a flashy unified chat.
   Treat identity-provider provisioning as a requirement only for any
   advertised authenticated workflow; local operator-only demo is
   independently valid.
5. **Release preparation.** Fix contradictory legacy docs, add credible
   architecture diagram and actual screenshot/video once captured by
   operator, choose an explicit LICENSE with owner consent, add CHANGELOG,
   complete all checks and tag `v1.0.0` only when each gate has evidence.

## Portfolio v1 versus later full platform

**Not required for honest portfolio v1:** NVIDIA GPU or vLLM, paid hosted
GPU infrastructure, Kubernetes, commercial billing, public signup,
global traffic routing, production SLA, or multi-region high availability.
They belong to optional v2 when the underlying model serving work is
well-proven. They must not be advertised as present.

**Always required for any public deployment:** verified hardened
authentication, HTTPS/mTLS boundary where needed, protected secrets,
tenant isolation, backups, rate limits and security review. The current
repo defaults to loopback-only development. **Do not expose it online.**

## Evidence provenance rules

- `tests/e2e/test_real_ollama_acceptance.py` uses synthetic injected
  HTTP fixtures. Its CI success is a contract check, not a local M2 run.
- `python3 -m tools.demo.real_ollama --expected-digest <hex> --output
  reports/local/real-ollama-smoke.json` issues three bounded actual
  inference requests to already-running localhost services. It is an
  operator-observed smoke, not proof of GPU execution/TTFT/p95, and must
  never be presented as a benchmark.
- Never publish private access tokens, raw model output, local user
  data, unreviewed reports or model-license-restricted artifacts.
- Public measurement claims link to a reproducible **sanitized** aggregate
  report and name run date, model digest, quantization, hardware/runtime,
  concurrency, run count, methodology, drawbacks and experimental limits.

## Current starting point

Historical starting reference `6546a93` (2026-10-18 JST), **not the current `main` HEAD**: Go gateway, optional
tenant/project control, Redis quotas, bounded SSE and aggregate model
usage observation, protected local model/deployment workflows, 11 CI
jobs, offline mock demo, and operator-reported early parallelism
study. This is a capable **local research prototype**, not yet P1–P6
portfolio v1 release. Do not label a percentage as an automated,
objective completion score.
