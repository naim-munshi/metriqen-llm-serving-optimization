# Metriqen — Current evidence and limitations (2026-10-31 review)

## Current source/owner gate status (supersedes older dated milestones)

**Portfolio v1.0: NO-GO.** At this review, current
[main `7c403b9`](https://github.com/naim-munshi/metriqen-llm-serving-optimization/commit/7c403b99ec59d4aec10dce457f388e82b1935745)
(PR [#172](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/172))
completed **13/13 post-merge GitHub CI jobs**. Open pull requests: 0.
The original source/docs are published under [MIT](../LICENSE), but
repository `main` was still **unprotected**, active rulesets: 0,
and there is **no tagged v1.0.0 release**. A green source test is
neither Mac Safari/Metal verification nor independent live tenant security.

The [short remaining Mac/owner handoff](operations/mac-only-remaining-acceptance.md)
is the operative to-do checklist. It does **not** repeat operator-observed
P1 native/Go/alias lifecycle PASS, the P1 private digest-consistency PASS,
or P5 real Ollama outage/HTTP502 → restored inference and real SSE
`[DONE]`/admission-release PASS. PR #169 added **synthetic** upstream
error-response redaction regression tests; PR #171 added real **local HTTP/TCP
stream-disconnect integration** tests on a fake upstream for both 1 KiB and 4
KiB relay buffers, with exact slot-release and follow-up-chat accounting.
These source tests do **not** attest a real Ollama cancellation by
themselves. **On October 31**, the operator separately ran the real Mac
bounded client-disconnect/recovery CLI with `--execute` and private pinned
digest; its Terminal reported **PASS** for gateway cancellation,
admission-slot release and recovered JSON inference, with a private
sanitized report saved. The report bytes have not been independently
inspected. **Actual Mac HTTP502 error *body* redaction is still unverified**;
server-side native Ollama task termination is not attested.
Historical P2 large A/B/A/B drift/AC contamination and P3 negative FIFO
goodput tradeoff remain **valid warnings, not speedups**.
P4's headless Chromium CI at 320px/390px on all three UIs is not real
Safari/mobile/keyboard approval. Owner-supplied **real Safari 390×844**
screenshots of Operations and Model Manager show readable headers/cards
and initial controls without apparent page-wide overflow. The remaining
below-fold, internal horizontal scrolling, 320px real Safari, Tab/Shift+Tab,
VoiceOver, signed-in model-management journey and sanitized public media
review have not been independently demonstrated. P6 owner license-choice is settled;
admin branch rules, approved real media, final acceptance and tag are not.

**Current release blockers:**
P1 final owner-approved authentic browser media;
P2 AC/slot/thermal-controlled prospective A1/B1/A2/B2;
P3 one reproducibly measured Metriqen-owned code/policy change (or
explicitly revised contract and accurately qualified negative study);
P4 genuine Mac Safari 320px and full 390px scrolling/keyboard/VoiceOver;
P5 actual 502 error-body inspection, and configured identity/tenant
authorization only if those opt-in features are claimed;
P6 enforced branch rules, audited approved artifacts, release-SHA CI
and explicit owner signoff. Keep all localhost services private.

---

## Historical 2026-10-28 evidence summary (preserved)

# Metriqen — Current evidence and limitations (2026-10-28 update)

## Verified operator observations, remaining portfolio gates

**Release decision: NO-GO.** The latest merged source milestone at this
review is [PR #161](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/161)
at `8979aec1b556cd0255f75982f45f36ecc427c042` (12/12 successful
CI jobs on its final PR head). CI is synthetic/ephemeral source evidence,
not operator-Mac GPU, browser or production auth attestation.

| Gate | New 2026-10-28 operator evidence | Still open |
| --- | --- | --- |
| **P1** | Native real Ollama + Go JSON/SSE, a real Model Manager chat, isolated fresh alias `acceptance-028e486b1e` completing **11/11 real Stop/Start/Promote/Rollback checks** on approved `llama3.2:1b` ↔ `llama3.2:latest`, and distinct private smoke/full-lifecycle reports passing deterministic pinned-digest/required-check **CLI consistency review** | Independent Mac/browser approval of promotion/rollback and sanitized genuine screenshots; no hardware attestation |
| **P2** | Historical Ollama parallelism experiments retained, including large drift and contaminated power phases | New uninterrupted AC-only A1/B1/A2/B2 slot/thermal checks; no defensible speedup yet |
| **P3** | FIFO overload and SSE buffer studies reveal tradeoffs, not a proven serving speedup | Attributable Metriqen-owned optimization test, matched quality/regressions or explicit negative conclusion |
| **P4** | Private actual Mac desktop UI screenshots (Go Operations/Model Manager), real inference and routing views. PR #165's **13-job CI** includes actual **synthetic Chromium** rendering of all three existing local consoles at 320/390px, Go 768/1440px, keyboard skip/navigation, Pause/Refresh and no page-wide mobile overflow; no real model/auth traffic is used. | Human Mac Safari 390/320px, Tab/Shift+Tab/Enter, Pause/Refresh/VoiceOver checks and owner-approved public demo media **remain pending** |
| **P5** | **7/7 required healthy GET** and **10/10 unauthorized/invalid Bearer HTTP401** checks; Ollama, Go and FastAPI listener snapshots IPv4 loopback on ports 11434, 8081, 8000. Real graceful Ollama outage yielded gateway upstream HTTP503 and failed chat HTTP502 while Go PID `39701` stayed live; restore yielded upstream HTTP200 and **real nonstream chat PASS** with same PID. Three distinct private health-state reports passed offline healthy/outage/recovered review. Subsequent bounded real SSE chat gave nonempty delta, terminal `[DONE]`, exactly one admitted stream, validated counters, and **active slots released to zero** | Sanitized 502 *response body* not yet checked; live SSE client cancel not exercised; opt-in OIDC/tenant/PostgreSQL/Redis authorization and lease security have no real Mac end-to-end signoff |
| **P6** | All above summarized in [tracking issue #81](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81), private reports/screenshots retained; owner-directed [MIT License](../LICENSE) selected Oct 29 for original repository-owned software, excluding independently licensed dependencies/model weights/datasets. | Release SHA CI, approved real demo media, branch protection/rulesets, and P1–P6 manual acceptance; no v1.0.0 |

**P6 owner-license update (Oct 29):** The repository owner requested a license; Metriqen original eligible software is now published under [MIT](../LICENSE), Copyright © 2026 Md Naim. This is a permissive copyright grant for eligible repository materials, not for third-party packages, separately licensed models, nonredistributable data or private evidence. Branch protection, release evidence and signoff remain blocked. See [licensing boundaries](operations/licensing-and-third-party.md).

**P4 browser-source milestone (Oct 29):** [PR #165](https://github.com/naim-munshi/metriqen-llm-serving-optimization/pull/165) successfully added a thirteenth CI job that renders the real static HTML/CSS/JS of the Operations Dashboard, Model Manager and optional Account Console using headless Chromium and intercepted anonymous fixture data. No actual Mac Safari screen, logged-in account or private Ollama inference was observed in this job. The first 12 existing CI jobs remained unchanged. The Go dashboard's viewport/keyboard tests, model-manager anonymous form and account signed-out flow passed on the PR head. This is **automated source-layout QA, not P4 final manual acceptance**.

**Evidence boundary:** The offline three-report P5 reviewer verifies HTTP-health
snapshot consistency only. The recovered chat and unchanged gateway PID were
observed **separately**. Earlier mistakenly named `p5-outage-*` reports
actually captured healthy service and are *excluded* from verified outage
evidence. None of these observations measure GPU execution, TTFT/p95/RPS,
hosted-tenant security or production availability. Port 8000 optional account
console HTTP404 means no configured OIDC account UI in that local run; its
absence must not be reported as a successful OAuth security test. These
local unguarded-development listeners must **never be publicly exposed**.

Existing October 27 findings and earlier experimental details are preserved
below as dated history; where observations disagree, the above October 28
operator updates supersede the older state notes only. The formal finish
line remains [P1–P6 acceptance](PORTFOLIO_V1_ACCEPTANCE.md).

---

## Historical October 27 audit (preserved)


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
