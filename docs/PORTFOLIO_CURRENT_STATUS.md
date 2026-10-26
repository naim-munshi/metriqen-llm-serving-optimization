# Metriqen — Current evidence and limitations (2026-10-27 review)

## October 27 source audit and release decision

**Release decision: NO-GO pending operator/owner evidence.** The repository has **not** passed human-reviewed Portfolio v1.0 P1–P6
acceptance and **has not published `v1.0.0`**. The latest independently
verified merged GitHub commit at this review is
[`61d62ce`](https://github.com/naim-munshi/metriqen-llm-serving-optimization/commit/61d62ce577826904b4d83991793ee6a4873cd91d)
with [12/12 successful post-merge CI jobs](https://github.com/naim-munshi/metriqen-llm-serving-optimization/actions/runs/40159749284).
This is source and synthetic/ephemeral-service evidence, not an actual
Mac/GPU benchmark or hosted deployment.

**Recent code improvements (#146–#155):** safe isolated operator alias
setup (Mac execution still pending), sanitized HTTP error-handle cleanup,
fail-closed duplicated/contradictory Ollama model identity, explicit
SQLite connection closure, race-safe deployment Promote/Rollback/Start,
atomic deployment write receipts and exact reported model-tag matching
at the FastAPI → Go completion boundary. These make local routing
and error handling more robust but do not establish runtime attestation,
semantic AI quality or a serving speedup. The original `assistant`
history remains protected; the new `acceptance-test` helper has not
yet been operator-executed on the Mac.

**Release blockers:** P1 final real UI/isolated lifecycle evidence;
P2 independently reviewed live-slot/AC/thermal controls and replication
drift; P3 one credible Metriqen-owned measurable serving-path result
without hidden regressions; P4 real desktop/mobile/keyboard captures and
privacy-approved demo video; P5 operator-enabled auth/recovery acceptance;
P6 owner's explicit license choice, verified `main` ruleset (last API
read `protected=false`), final media and release authorization.
The [exact final owner handoff](operations/v1-final-closeout.md) and
[release tracker #81](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81)
are the release decision references. Public hosted GPU, Google OAuth and
production internet access remain separate optional v2 issues and are
not v1 blockers.

## Historical October 24 feature/evidence inventory (measurements unchanged)

**Current category:** tested, local-only LLM serving research prototype and
AI Infrastructure Engineering portfolio candidate. **Not yet portfolio v1.0
released; not a production SaaS.** This is the current-state summary; the
[October 14 historical status](PROJECT_STATUS.md) remains an archive, not
a current feature inventory.

The latest source milestone independently verified before this documentation update
is `3fe88796584140705c2989059e251bf6933723cb` (PR #139). Its
[12-job post-merge main GitHub CI](https://github.com/naim-munshi/metriqen-llm-serving-optimization/actions/runs/40091080351)
passed **12/12** on October 24, including the new public tracked-file
safety check. This is a dated source milestone, **not** a guarantee
about the head of any future branch or commit. These are source and synthetic CI checks,
not independent owner-Mac or browser acceptance. The last **separately supplied Mac source test transcript** ran at
earlier SHA `243bd1f` (545 FastAPI, 70 E2E, 268 benchmark, 16 NLP,
17 Node = 916 explicitly passed; plus Go race/vet, 20 skips).
**Hosted CI tests are not real Mac/GPU/Google login attestations** and
Mac must pull latest main for current-source verification.

## Evidence-linked feature matrix

| Feature | Actually implemented | What is still unproven |
| --- | --- | --- |
| Go local inference | Ollama-compatible JSON/SSE chat, request/body limits, 4 active slots, opt-in FIFO 0–32 waiters, cancellation, bound timeouts/error mapping | Internet-facing authenticated LLM serving, production SLA |
| Real local model execution | Operator-reported successful Mac Ollama + Go launch and bounded real inference smoke | Independently published operator review, prospective long-run reliability |
| Local model lifecycle | Opt-in SQLite digest approval, Ollama preload/unload, named aliases, Stop/Start/Promote/Rollback, operator-reported private real lifecycle consistency | Immutable tag locking at every path; production model orchestration; completed P1 browser gate |
| Diagnostics | Process-local admission/outcome/upstream failure/categorized token usage counters and three separate local consoles | Persistent metrics/alerting, audited real-browser accessible/multi-device screenshots |
| Repeated performance experiment | Fixed datasets, timed repeats, RPS, TTFT, p95, usage and exact/normalized quality; versioned synthetic evidence integrity reviewers | Significant repeat drift; actual runtime slot/AC documentation gaps; **no causal serving speedup** |
| SSE Go relay optimization candidate | Opt-in default 1 KiB versus 4 KiB relay read buffer, executable identity and fingerprint checks, 4-phase private collector, cross-phase same-binary checks, offline missing token-usage and within-setting drift review; synthetic tests CI-green | **No valid fresh owner-Mac A1/B1/A2/B2**; old A1 was served by an unrelated `go run` cache executable after port 8081 bind conflict, so it is excluded. No quality-preserving causal performance gain attested |
| FIFO admission A/B | 864 local operator-reported requests; queue depth 0 vs 2 increased accepted completions 144→252/432 but reduced offered goodput at every tested concurrency and worsened overload tail latency/TTFT | No quality-preserving faster inference or completed P3 |
| Tenant control code | Opt-in JWT pinned issuer/JWKS verification, PostgreSQL organizations/projects/teams and RBAC, Redis quotas/leases/reservations, PG+Redis CI | No real public hosted two-user end-to-end auth/isolation acceptance |
| Google login | Opt-in localhost Code+PKCE BFF, nonce-verified Google ID token, opaque Redis session; operator-only pinned Google public JWKS rotation tool | OAuth Web Client credentials/consent, actual live Google login, production HTTPS cookies/proxy/JWKS refresh |
| GPU scaling design | Pure offline cost/queue/cooldown capacity planner plus generation-fenced **simulation** of intended worker count | **No actual GPU provider, workers, autoscaling events or enforced billing cap** |
| Public hosting safeguards | Docker/Go/FastAPI localhost restrictions and static regression tests | Separate production-only HTTPS ingress/BFF, TLS databases, private model workers, real security review |
| Recruiter demo | Offline deterministic real-Go fake-model demo, 3 UIs, operator-run real Mac demo launcher | Video, privacy-approved original screenshots, keyboard/mobile real visual QA |
| Release | Public repo, documentation, 12-job CI, changelog, six-gate release contract; force-tracked local/private path guard | Owner LICENSE decision, unprotected `main`, GitHub Security alerts verification, verified P1–P6 evidence and v1.0.0 GitHub release |

## Performance findings, not claims

- [October 13](benchmarks/experiments/2026-10-13-ollama-parallelism.md):
  Ollama `OLLAMA_NUM_PARALLEL` setting change saw +34.235% RPS at
  offered concurrency 2, **but** −3.895% at concurrency 1. Not a
  Metriqen-owned algorithm or causal improvement.
- [October 20 A/B/A/B](benchmarks/experiments/2026-10-20-ollama-parallelism-abab.md):
  1,296/1,296 measured requests succeeded; **large same-setting drift and
  reversed candidate effects** mean no reproducible speedup.
- [October 21 FIFO A/B](benchmarks/experiments/2026-10-21-fifo-admission-overload.md):
  144/432 → 252/432 completed and 288 → 180 HTTP 429 responses,
  **with worse goodput at every offered concurrency** and degraded
  successful-only overloaded p95 latency/TTFT. A scheduling trade-off,
  **not** a verified optimization speedup.

- **October 23–24 SSE experiment safety:** The operator-built gateway
  failed to bind `127.0.0.1:8081`, and a previously running Go build-cache
  executable served the old 36/36 A1 successes. This is **invalid** as a
  same-binary A1 comparison baseline. [PR #131](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/131)
  added active executable identity verification; [PR #132](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/132)
  expanded failure-path tests; [PR #133](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/133)
  exposed usage completeness and within-setting drift in the offline
  review. **No fresh real Mac A/B/A/B comparison has yet been supplied
  for acceptance**, and P3 remains unapproved. Old private evidence
  stays unchanged and excluded from speedup claims.

## Recent source-level security and benchmark fixes (October 24)

- [PR #135](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/135):
  the dedicated SSE operator collector fails closed unless the running
  Gateway explicitly uses the pinned localhost Ollama origin; the runbook
  no longer silently strips inherited auth environment variables.
- [PR #136](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/136):
  macOS kernel `libproc.proc_pidpath` checks the actual main executable,
  not just an `lsof -d txt` mapped-file entry. Native Mac acceptance is still
  required; Linux CI uses synthetic native-call fixtures.
- [PR #137](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/137):
  source preflight refuses a Gateway PID with both localhost and any
  extra wildcard/public/IPv6 listening socket.
- [PR #138](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/138):
  CI now rejects tracked `.env`, private report, local SQLite state and
  known credential-file paths. **Filenames only**: not Git history,
  content-based secret detection or proof that GitHub secret-scanning
  alerts are enabled.
- [PR #139](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/139):
  the Python SSE benchmark runner no longer considers `[DONE]` alone
  sufficient: it validates clean transport EOF and rejects post-terminal
  errors/duplicate events before counting success and token usage.

The repository's `main` was **not branch-protected** at the last
GitHub inspection. Repository ruleset enforcement and the owner's license
choice remain separate human/admin actions. Nothing in these source fixes
attests actual Mac Ollama speedup or completes P1–P6.

## Highest-value completion order

1. **P3:** pre-register one Metriqen-owned serving-path hypothesis,
   implement one isolated code/policy change, rerun same real Ollama
   workloads under matched conditions; preserve every error, p95/TTFT
   regression, reference-quality score, actual slot/power/thermal notes.
   Report neutral/negative results as such.
2. **P1/P4/P5:** independently inspect native model and gateway, full
   local rollback, failure/recovery and genuine desktop/mobile/browser
   keyboard screenshots; redact identities and secrets.
3. **P2:** review experimental drift and missing runtime observations.
   Repeat on current software if evidence cannot meet the
   [strict P2 gate](PORTFOLIO_V1_ACCEPTANCE.md).
4. **P6:** decide license/proprietary status; protect `main`, cleanup
   stale pages, publish original walkthrough and evidence, only then
   tag a real `v1.0.0` release.
5. **Separate optional v2:** real Google OAuth/hosted two-account HTTPS,
   private authenticated GPU workers and cost-enforced autoscaling.
   See [#114](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/114),
   [#115](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/115),
   [#116](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/116).
   **None are prerequisites for a truthful local portfolio v1.**

The authoritative acceptance contract remains
[P1–P6](PORTFOLIO_V1_ACCEPTANCE.md), tracked in
[issue #81](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81).
Do not turn source-green CI, a private unreviewed report, or simulated
GPU worker state into a public release claim.
