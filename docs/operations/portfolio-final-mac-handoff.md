# Portfolio v1.0 — final operator handoff (Mac tasks only)

**Source code and synthetic CI are not a portfolio release.** This is the
short, ordered checklist that must be completed on the actual operator
Mac before the recruiter-facing `v1.0.0` tag can truthfully be created.

The fixed scope/definition of done is
[Portfolio acceptance P1–P6](../PORTFOLIO_V1_ACCEPTANCE.md);
the tracking issue is
[GitHub #81](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81).
Do not silently expand the v1 scope into public SaaS/GPU/vLLM/Kubernetes
or claim memory/quality/GPU metrics that were not measured.

## A. Before enabling any service

1. On the Mac, `git pull --ff-only origin main` and record the exact
   `git rev-parse HEAD` commit. Check `go version`, `python3 --version`,
   `ollama --version` and available disk/RAM.
2. On macOS, create/activate a dedicated environment and install the control
   API development dependencies before `make portfolio-ci` (Python 3.12+):

   ```sh
   python3 -m venv .venv
   . .venv/bin/activate
   python3 -m pip install -e './services/control-api[dev]'
   make portfolio-ci
   ```

   The Makefile defaults to `python3`, not the sometimes-absent macOS `python`;
   set `PYTHON=/absolute/path/to/python3` to override. If HTTPS package
   installation fails due to an untrusted PyPI TLS certificate, follow the
   [secure Mac pip certificate diagnosis](macos-python-tls.md) instead of
   disabling SSL verification. The hosted CI validates Python 3.13; the
   operator Mac has Python 3.14; on October 23 the operator provided
   a successful `make portfolio-ci` transcript on earlier main commit
   `243bd1f` (916 explicit passing source tests plus Go race/vet).
   That is real **source-test execution on the Mac**, not independently
   verified model/slot/browser acceptance, and it predates later changes.
   Go and Node must also be installed. PostgreSQL/Redis CI jobs on
   GitHub use ephemeral isolated services; local `make` alone does
   **not** recreate those integrations unless the operator starts them
   separately.
3. Inspect repository `SECURITY.md` and keep all UIs, APIs, Ollama,
   PostgreSQL and Redis bound to **localhost**. **Never expose this
   development platform through tunnels or open public ports.**
4. Confirm the local model's software license before publicizing
   screenshots/output. Never put user prompts, JWTs, operator tokens,
   browser session cookies, DSNs, screenshots with identities or
   full private benchmark logs in Git commits.

### Optional read-only P4/P5 Mac service snapshot (no inference, no AC)

After the local services are **already running**, the
[restricted localhost reviewer preflight](local-readonly-review-preflight.md)
can check Go/FastAPI console HTTP availability, Ollama API version,
gateway dependency health and the unauthenticated model registry's expected
HTTP 401 **without** loading a model, supplying a credential, navigating to
a real identity provider or changing process state:

```bash
python3 -m tools.demo.local_readonly_review
python3 -m tools.demo.local_readonly_review --execute
```

Optional `--output reports/local/unique-review.json` creates a new
owner-private, sanitized source-independent snapshot. It **does not**
check actual browser layout, local network binding, real model inference,
enabled JWT/Redis/PostgreSQL identity, latency or successful recovery;
it cannot close P1, P4 or P5.

## B. P1 — real inference and model-management acceptance

With Ollama and the Go inference gateway already running locally
per [local inference](local-inference.md):

```sh
# Replace with actual digest obtained from /api/tags, never a fake SHA.
curl -fsS http://127.0.0.1:11434/api/tags
python3 -m tools.demo.real_ollama \
  --model llama3.2:1b \
  --expected-digest YOUR_REAL_LOWERCASE_64_CHAR_MODEL_DIGEST \
  --output reports/local/real-ollama-smoke.json
```

**Operator acceptance:** inspected the actual processes, verified nonempty
native Ollama generation, gateway nonstream/SSE, exactly matched loaded
digest, clean terminal [DONE], the gateway metrics and returned
`passed: true`. The checker is three bounded requests and **not** a
real throughput/p95 benchmark, GPU/Metal verification or proof of
runtime/model attestation.

To reduce repetitive manual HTTP validation, the [P1 local deployment lifecycle checker](local-deployment-acceptance.md) offers a read-only preflight by default, with an explicit `--execute` option for Stop → denied chat → Start → chat and optional Promote → chat → Rollback → chat. It requires an already approved and loaded second model for the full test and stores only a private sanitized JSON under `reports/local/`. This tool **does not replace independent visual browser review**, may temporarily change routing state, and cannot guarantee restoration if model memory is evicted.

After both private real-model smoke and **full** Stop/Start/Promote/Rollback reports exist, run the [offline P1 private evidence consistency reviewer](p1-evidence-review.md) to match the original pinned model digest and ensure no lifecycle assertion is missing. The reviewer writes an owner-only sanitized summary but **does not** attest Mac execution or satisfy the P1 browser gate. On small-memory Macs the sequential loading option only requires each model at its switch, not both models simultaneously.

Complete the separate **operator-only** model-management steps in the
actual browser and note each result: installed model → pin digest →
approve → load → invoke → promote → stop/start → rollback. Local model
approval alone is **not** project-scoped Go gateway authorization.

## C. P2/P3 — controlled, falsifiable optimization evidence

To reduce repetitive manual steps without compromising the prospective design,
use the [P2 one-phase operator runner](../benchmarks/phase-runner.md).
It calls the strict preflight, nine real benchmark runs and separate quality
checks; model process configuration, slot verification and final human
attestation remain manual, and it will not modify Ollama settings.
After all four private phases succeed, `tools.benchmark.four_phase_review`
can bind preflights, validate all raw reports and render three private
aggregate reports in a single **offline** command. It does not attest
actual Mac runtime slots or satisfy P2/P3 human acceptance.

Use [preflight/phase-binding](../benchmarks/phase-preflight-binding.md),
[the 4-phase A/B/A/B protocol](../benchmarks/abab-replication.md),
[raw evidence generation](../benchmarks/real-model-evidence-bundle.md)
and [matched comparison](../benchmarks/controlled-comparison.md).
Run four separate A1/B1/A2/B2 phases with explicitly recorded
`OLLAMA_NUM_PARALLEL`, actual logs/slots, AC power and thermal notes;
keep pinned model digest and exact dataset/generation settings.

**Do not modify raw outputs to pass tests.** Private run reports belong in
`reports/local/`, ignored by Git; aggregate reports must be
sanitized and explicitly attributed to the observed hardware, run date,
model revision, quantization, software versions and number of repeats.

For a bounded Metriqen-controlled FIFO queue-policy overload candidate,
see the [queue-depth 0 vs 2 protocol, guarded per-phase Mac collector and strict offline tradeoff review](../benchmarks/admission-overload.md).
The `tools.benchmark.admission_phase` CLI performs fail-closed AC/digest/
gateway-queue preflight, 12 timed overload runs and separate 36-answer
quality capture for each manually configured phase, with an additional
power/config check afterward. The operator must preserve actual n_slots
logs and use **unchanged** Ollama parallelism in both conditions; the
collector does not manage processes or authenticate uninterrupted power.
The review counts every HTTP 429 and reports success and latency together;
neither helper substitutes for human-reviewed independent model-quality
and thermal observations or establishes P3 success. A negative result is valid engineering evidence; do not
claim a queue speedup merely because Go unit tests pass.

For any separate **Metriqen-controlled** optimization, run the
[conservative tradeoff screen](../benchmarks/portfolio-optimization-signal.md):

```sh
python3 -m tools.benchmark.optimization_signal \
  --baseline reports/local/baseline/evidence-manifest.json \
  --candidate reports/local/candidate/evidence-manifest.json \
  --change "Exact Metriqen Go code or serving policy changed" \
  --target-concurrency 2 --min-rps-gain 5 --max-regression 5 \
  --output reports/local/optimization-signal.json
```

This is **descriptive**, not causal/attested. Show all concurrency 1/2/4
regressions; include quality results, p95 latency, TTFT, RPS, token
coverage and memory/power notes where genuinely available. A Go CPU
microbenchmark improvement is distinct from an end-to-end LLM throughput
speedup. The previous Ollama parallelism test is a *configuration*
experiment, not a newly implemented Metriqen serving algorithm.

## D. P4 — actual browser review and demo capture

Follow [the 3-console reviewer journey](portfolio-local-reviewer-journey.md)
in Safari/Chrome at desktop and mobile widths. Verify labels, keyboard
focus/Tab navigation, readable tables, loading/unavailable/error states,
read-only interactions and no cross-console credential sharing.
Use the model-manager/deployment controls with actual installed model
only after local operator approval.

Capture authentic screenshots and a short video (e.g. 2–3 minutes)
showing legitimate real operations: dashboard, approved model, actual
chat result, monitoring, and a successful rollback. **Redact all secrets,
personal data, prompts/model output unless permissioned.** Never
generate fabricated "real Mac" screenshots based on synthetic mock tests.
Link the sanitized assets in the final README only after human review.

## E. P5 — local reliability/security and recovery

An optional [offline, read-only three-snapshot outage/recovery HTTP-health evidence reviewer](p5-readonly-recovery-evidence.md) can cross-check operator-created baseline/outage/recovered local GET reports without stopping services or performing inference. Even a valid health transition does **not** attest successful post-outage chat, gateway process continuity, cancellation, tenant security or completed P5 acceptance. Only run a real outage experiment with explicit operator control on an idle localhost-only environment.

Read [Security](../../SECURITY.md),
[distributed request leases](distributed-project-inflight.md),
[local JWT/digest authorization](project-inference-authorization.md),
and [operator recovery checklist](local-inference.md).

- Verify unavailable Ollama results in bounded, sanitized errors and
  automatic gateway admission release. Restart Ollama **manually** only
  after preserving relevant logs; ensure subsequent request succeeds
  without restarting the Go gateway.
- Verify streaming cancellation/missing [DONE] fails cleanly and the
  next request succeeds. The Go synthetic process recovery test exercises
  this contract in CI; **Mac is not tested by CI**.
- If running a guarded project mode: test signed identities, revoked
  membership, wrong project and mismatched model digest. If OIDC,
  PostgreSQL, Redis or service-to-service secrets are absent, state
  that those particular operator validations were not run; do not
  claim production security or publicly expose the service.
- Record gateway runtime/version, component settings actually enabled,
  data redaction review, CI SHA and the recovery observations.

## F. P6 — release review and license decision

Follow the [owner-operated repository branch hygiene procedure](repository-branch-hygiene.md)
for an audit-before-prune cleanup of stale merged PR branches. No
branch-ref cleanup is a substitute for local P1–P5 evidence; never delete
unmerged unique commits as a release shortcut.

1. Update `README.md`, [engineering case study](../PORTFOLIO_CASE_STUDY.md)
   and `CHANGELOG.md` with only verified operator evidence, actual
   screenshots and versioned benchmark aggregate reports.
2. The repository owner decides the licensing strategy (**MIT, Apache-2.0,
   another eligible license, or keep proprietary**). Do **not** add a
   license or claim permission without the owner's decision; check
   dependencies, original datasets and model redistribution restrictions.
3. Re-run full GitHub CI on the *actual release candidate commit*;
   check that all jobs pass and GitHub Issue #81 has verifiable direct
   evidence links for **every** P1–P6 condition.
4. Only after independent human review and all gates pass:
   tag `v1.0.0` and publish a GitHub release with a concise change
   summary, actual limitations and demo links. No published release
   is implied by a passing source-only CI run.

**Current state:** source-only GitHub work can be advanced without the
Mac, but these operator-only evidence steps are intentionally **not
auto-completed**. Once evidence is available, the README and tracker
can be updated accurately. No release date or performance result is
promised in advance.
