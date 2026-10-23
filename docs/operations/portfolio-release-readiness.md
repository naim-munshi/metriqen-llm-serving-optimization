# Portfolio v1.0 readiness: source-only audit

The [six acceptance gates](../PORTFOLIO_V1_ACCEPTANCE.md) still require
independently reviewed **real Mac operator evidence**. Running tests,
a green hosted CI badge, a LICENSE file or model-serving feature count
cannot substitute for that evidence.

From the repository root, run this optional **offline, read-only** status
command (no secrets, GPUs, Google API, browser or network):

```sh
python3 -m tools.demo.portfolio_readiness
# Optional machine-readable output:
python3 -m tools.demo.portfolio_readiness --json
```

Both forms exit with **code 2** while release acceptance is blocked. That
intentional nonzero status prevents scripts from interpreting a status
report as an approval. The command *does not update P1–P6 checkboxes*,
commit evidence, create a release or make any network requests.

The JSON reports exactly six gates, each
`"status": "manual_acceptance_required"`, and separate
`"release_approved": false` and
`"portfolio_v1_released": false` fields. It checks whether expected
**public** documentation is present and whether a prospective owner
license/proprietary-rights decision file is present. Existence of a file
**never** approves its contents or the license. An issue comment or a
private `reports/local/` screenshot is not evaluated by this script.

## What remains operator-only

- P1: inspect pinned real Ollama native/Go nonstream/SSE, digest,
  approval, real deployment Stop/Start/Promote/Rollback and approved
  real browser evidence.
- P2: review four-phase A/B/A/B runtime slots, power, thermals, drift,
  reproducible quality and full failures. Re-run if existing evidence
  fails the gate.
- P3: select one attributable Metriqen-owned serving-path change and
  measure honestly on the same real model/workload, including p95
  latency/TTFT/goodput regressions and answer quality.
- P4: verify real keyboard/mobile UI and privacy-review a short original
  recruiter demo video and screenshots.
- P5: verify actual configured local identity/Redis/PostgreSQL, timeout
  and upstream recovery, cross-project isolation; CI tests alone cannot
  attest enabled Mac configurations.
- P6: owner license decision, repository branch protection, release
  documentation/evidence links and tagged `v1.0.0` only **after**
  all gates have been independently signed off.

The official release owner/maintainer must make a deliberate sign-off
decision. An approved future audited release workflow would need explicit
human-reviewed evidence metadata, not a change of this script to
return zero when CI passes.

For the status of deployed Google/GPU/public hosting work, consult the
[current evidence matrix](../PORTFOLIO_CURRENT_STATUS.md). Cloud v2
capabilities are **not** prerequisites for an honest local portfolio v1.0.
