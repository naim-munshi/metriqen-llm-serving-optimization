# Metriqen — only the remaining Mac/owner work (October 29, 2026)

**This is the short handoff after the GitHub-only work.** Complete the
independent [Portfolio P1–P6 contract](../PORTFOLIO_V1_ACCEPTANCE.md)
before making the `v1.0.0` tag. The [current evidence matrix](../PORTFOLIO_CURRENT_STATUS.md)
and [tracker #81](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81)
contain already-observed results; never rerun a previously accepted test
just to populate another private report. No automation on this page
connects to the operator's computer from GitHub or a phone.

## Already done — don't repeat without a reason

- **P1 private CLI:** real native Ollama/Go and browser chat, isolated
  `acceptance-028e486b1e` eleven-check Stop/Start/Promote/Rollback,
  and private P1 smoke/lifecycle consistency **PASS**. Preserve the
  existing protected rollback history and private files.
- **P5 real Mac:** seven required healthy GET checks; ten negative local
  operator auth 401 checks; all three listeners on IPv4 loopback.
  Actual Ollama outage caused upstream HTTP503 and failed chat HTTP502
  while Go PID 39701 continued. Manual Ollama restoration produced
  upstream HTTP200 and recovered nonstream chat. The three private
  baseline/true-outage/recovered reports passed **offline HTTP-health
  consistency**. A separate real SSE request returned nonempty delta,
  `[DONE]` and exact counters with idle slots released. **Not tested
  by these results:** sanitized 502 *response body*, client-disconnect
  cleanup, OIDC/PG/Redis authorization configured on the Mac.
- **GitHub-source/P4 synthetic CI:** pinned Go race/vet, ephemeral
  PostgreSQL/Redis, security/unit tests, and Chromium rendering of all
  three console UIs at 320/390px plus Go Operations at 768/1440px.
  Headless fake-data tests are **not** Mac Safari/VoiceOver.
- **P6:** owner-directed [MIT code license](../../LICENSE) is in
  `main`, with model/third-party exceptions documented. Public
  `main` was last observed **unprotected**; no `v1.0.0` tag exists.

## 0. One-time Mac preparation (non-destructive)

1. Before touching any running service, back up the private
   `reports/local/` and original private screenshots without
   publishing them. Stop any other projects using Ollama before
   scheduling measured trials.
2. Run `cd ~/metriqen-llm-serving-optimization`; check
   `git status --short`. If clean, run
   `git pull --ff-only origin main` and `git rev-parse HEAD`.
   If local changes are present or fast-forward is blocked, **STOP**:
   do not reset, force-pull, delete a DB, or overwrite evidence.
3. Keep Ollama, Go and FastAPI exclusively on `127.0.0.1` (or
   `::1`), no tunnels or public listener. Record
   `ollama --version`, Go/Python versions, actual model tag/digest
   and wall-clock date. Ollama.app and a separate `ollama serve`
   must **not both** own TCP 11434.

## 1. P5 — one still-missing real client-cancellation run

Already-built tool, **dry-run first** (no network), then optional
explicit one-stream cancellation **only on an idle Mac gateway**:

```sh
python3 -m tools.demo.p5_stream_cancel_recovery
```

Use the pinned, private real-smoke model digest and the **exact
execution command** from
[P5 SSE client cancel procedure](p5-stream-cancel-recovery.md). It
sends a maximum 256-token SSE request and disconnects after the first
delta, checks cancellation/admission counters, then issues one
16-token recovered completion. No Ollama restart needed. Record only
the sanitized `reports/local/p5-cancel-recovery-*.json` result.
**Fail closed** if the model was evicted, another client is running or
the cancellation did not occur; do not fabricate a PASS.

**October 30 source-level enhancement:** Go CI also exercises an actual
ephemeral loopback HTTP client **TCP disconnect in the middle of an SSE
stream**. Its fake upstream must observe cancellation, the gateway must
release admission and reconcile metrics, and a subsequent JSON completion
must succeed, for both default 1024-byte and opted-in 4096-byte relay
buffer sizes. This is stronger than a synthetic canceled request context,
but it is **not** the real Mac/Ollama cancellation smoke above. The
operator's one real local test remains necessary for P5 signoff.

Go source CI now explicitly tests non-2xx and unavailable-upstream
**sanitized HTTP error envelopes** using a fake local backend. That
is useful source-level error-body evidence, **not** the actual Mac
HTTP502 response body. During any separately authorized real
failure/recovery test, review its HTTP status, JSON error code/message
and absence of local DSNs/prompt/error-body data; do not deliberately
redo the prior Ollama outage merely for another screenshot.

## 2. P4/P1 — actual browser and approval (no benchmark required)

1. Safari: `http://127.0.0.1:8081/console` (Go Operations) and
   `http://127.0.0.1:8000/console/models` (separate operator
   Model Manager). Never paste/share the operator token.
2. Safari → Settings → Advanced → enable **Show features for web
   developers**, then Develop → Enter Responsive Design Mode.
   Capture **390px and 320px** for each UI; verify no clipped cards,
   controls, horizontal whole-page scroll or unreadable text. The
   navigation rail may scroll horizontally by design.
3. With real keyboard, press **Tab, Shift+Tab, Enter**: test the
   skip-to-main link, model section navigation, Pause auto/Refresh
   and focus visibility. Test VoiceOver if available; note any
   limitations, including optional account console HTTP404 if
   OIDC is not configured.
4. Review the existing real desktop operator screenshots; approve
   **only carefully redacted authentic** media and a 2–3 minute local
   demo. Do not publish private screenshots automatically. Previously
   completed P1 lifecycle evidence is not rerun unless a distinct
   regression is found.

## 3. P2 — the missing prospective A1/B1/A2/B2 experiment

Follow [one-phase guarded Mac collector](../benchmarks/phase-runner.md)
and [A/B/A/B replication](../benchmarks/abab-replication.md). These
are separate, operator-controlled measurements, **not GitHub Actions
measurements**.

- Continuous AC power, quiet foreground environment, pinned digest,
  verified actual `OLLAMA_NUM_PARALLEL` process configuration **and
  live model-slot logs**, stable prompt/decoding, the exact fixed
  workload and independent quality data.
- Run **A1 (slots=1) → B1 (slots=2) → A2 (slots=1) →
  B2 (slots=2)**, preserving unique private directories even on
  errors. Do not let the collector restart Ollama/configure slots.
  Use the built-in power monitor and preserve *all* failures; an
  interrupted AC phase is **not valid evidence**.
- After each collector, follow its human finalize step only if the
  observed quantization, power, slots and runtime match. Run the
  existing **offline `tools.benchmark.four_phase_review`** once all
  four succeeded; report drift, RPS, TTFT, p95, token counts and
  separate quality without selecting only favorable runs.
- Historic Oct 20 A2 power contamination and large drift remain
  provenance, **not a clean replication**. Slot/power review cannot
  be faked by source CI.

## 4. P3 — attributable engineering finding, not an invented speedup

Follow [optimization signal runbook](../benchmarks/portfolio-optimization-signal.md)
and [P3 acceptance](../PORTFOLIO_V1_ACCEPTANCE.md).
Define exactly **one Metriqen-owned code/serving-policy variation**,
a prospective hypothesis and baseline/candidate acceptance criteria
before measuring the real model. Match P2 environment and independent
quality/failure checks. A **neutral or negative** attributable
tradeoff is valid engineering evidence, but does **not** satisfy a
claim that an optimization sped up serving. Historical FIFO 0→2
showed improved acceptance but **worse successful goodput at every
offered concurrency**; don't rebrand it as throughput acceleration.
Keep the approved default unless a candidate is honestly validated.

## 5. P5 optional configured identity security

The local operator-only demonstration can be judged without enabling
an OIDC provider. **Do not claim** production tenant identity,
cross-project PostgreSQL isolation, Redis quota/lease, HTTPS, Google
sign-in, or durable production deployment unless the relevant
opt-in runtime and negative authorization tests were actually
configured and passed. If those capabilities are showcased, use
the respective [security](../../SECURITY.md) and
[OIDC](browser-oidc-login.md)/[project guards](project-inference-authorization.md)
runbooks and record enabled/disabled feature flags. Keep all
backends loopback-only. GitHub ephemeral integration CI is not a
real Mac multi-tenant acceptance.

## 6. P6 — final owner-only GitHub administration and release

- **License complete:** MIT source [LICENSE](../../LICENSE);
  independently review model/dependency/dataset rights before
  bundling anything.
- **Branch protection pending:** use
  [owner GitHub Rulesets checklist](repository-branch-hygiene.md).
  Protect `main` against force push/deletion, require verified
  **13 current CI checks** and PR review suitable for the
  available collaborators/account plan. The connected development
  tool cannot enable administrative GitHub rulesets. Verify the
  GitHub API reports enforcement; green checks alone don't.
- With original/approved screenshot/video and sanitized real P2/P3/P5
  results, have the owner review each independent P1–P6 gate and
  record the exact release-candidate SHA in [issue #81](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81).
- Only when **all gates** are human-reviewed, CI on that exact SHA
  is green and branch protections are enabled, authorize creating
  the `v1.0.0` tag and GitHub release. Do not tag automatically.

**Current decision: NO-GO.** The user may share the honestly scoped
research prototype for job applications before `v1.0.0`, without
claiming production security, a causal GPU speedup or completed
recruiter release.
