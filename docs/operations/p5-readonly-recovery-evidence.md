# P5: Offline Ollama outage / recovery HTTP-health evidence review

**Scope:** an optional, strictly **offline** review of three existing
`local_readonly_review` reports. It does not simulate outages, stop Ollama,
restart the gateway, issue inference, measure recovery latency or validate
project/tenant security. **Synthetic CI passing is not real Mac acceptance.**

The operator has already produced successful P1 local-model, real chat and
Promote/Rollback evidence. Preserve that evidence and don't restart existing
services simply to run another P5 test. In particular, do not run a disruptive
outage experiment on a shared or production service.

## What the reviewer verifies

- Each source is a distinct, timezone-stamped, owner-private `0600` regular
  JSON under `reports/local/`, using the exact
  `metriqen.local-readonly-review.v1` source schema.
- The timestamps are strictly ordered: **baseline < outage < recovered**.
- The baseline and recovered snapshots show the entire set of seven *required*
  local read-only checks passing, including anonymous Model Manager HTTP 401.
- During the claimed outage, **the Go gateway is still alive** (HTTP 200),
  its UI remains available, its upstream health probe reports HTTP 503, and
  direct Ollama `/api/version` is unavailable. All snapshots come from
  explicit GET probes, with no Authorization header or inherited proxy.
- No source or output bytes containing upstream error bodies, credentials,
  identities, prompts or model replies are copied.

The reviewer intentionally **does not** mark P5 complete. It cannot verify
the Go process PID stayed identical, which component caused the outage, that
an admitted inference failed safely, that a slot/lease was released, or that
chat worked after recovery. It also cannot verify actual browser behavior,
OIDC/PG/Redis negative authorization tests, or whether a user forged the
JSON snapshots.

## Offline plan — no network, files or processes touched

```bash
cd ~/metriqen-llm-serving-optimization
python3 -m tools.demo.p5_readonly_recovery_review
```

If the operator **separately** chooses to test failure/recovery in an idle
localhost-only environment, they must own the intervention and preserve logs.
**This tool never stops or starts any process.** Use the existing
[operator recovery procedure](upstream-health-probe.md) for safe manual
Ollama process management, and [P5 requirements](portfolio-final-mac-handoff.md)
for bounded failure and successful inference checks. A failed HTTP-health
probe is not a reason to force-kill the runtime.

At three explicitly observed points, collect original evidence using the
already checked-in, GET-only eight-probe collector:

```bash
umask 077
python3 -m tools.demo.local_readonly_review --execute \
  --output "reports/local/p5-before-$(date +%Y%m%d-%H%M%S).json"
# Only during a separately authorized real outage:
python3 -m tools.demo.local_readonly_review --execute \
  --output "reports/local/p5-outage-$(date +%Y%m%d-%H%M%S).json"
# After the operator has restored Ollama and confirmed reachability:
python3 -m tools.demo.local_readonly_review --execute \
  --output "reports/local/p5-recovered-$(date +%Y%m%d-%H%M%S).json"
```

The expected `local_readonly_review --execute` **outage** exit status is
`2` when its required probes fail; it may still save the explicitly
requested private snapshot first. Don't treat exit `2` as automatic evidence
of an outage: inspect the saved report and the actual processes. Separate
manual action must occur **between** snapshots; three consecutive calls in
a healthy system cannot satisfy the transition.

With real paths (never use literal `<timestamp>` placeholders), run:

```bash
python3 -m tools.demo.p5_readonly_recovery_review \
  --baseline reports/local/p5-before-ACTUAL.json \
  --outage reports/local/p5-outage-ACTUAL.json \
  --recovered reports/local/p5-recovered-ACTUAL.json \
  --output "reports/local/p5-health-review-$(date +%Y%m%d-%H%M%S).json"
```

A valid sequence yields:

```text
P5 read-only HTTP health transition: PASS
Inference failure/recovery, gateway process continuity, and security: NOT VERIFIED
P5 portfolio acceptance: NOT YET COMPLETE
```

The reviewer exits `2` for missing or malformed evidence, unsafe paths,
failed transition assertions, bad timestamps or unsafe output. Reports are
created exclusively with owner-only permissions and never overwritten.
Do not commit `reports/local/`, publicize private evidence, or equate
`PASS` here with a production availability, GPU or benchmark claim.

**Next manual evidence before P5 acceptance:** record the gateway PID and
read-only counters before/after, bounded HTTP failure during the outage
(with sanitized response), completed inference after manual recovery with
the **same** gateway process, and the actual opt-in OIDC/PostgreSQL/Redis
security boundary tests if that deployment configuration is being claimed.
The default local operator demo does not imply these features are enabled.
