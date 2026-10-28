# P5 post-recovery bounded SSE + gateway admission release smoke

The operator has already observed a real Ollama outage, sanitized **HTTP 502
status only** on gateway chat, recovered `GET /health/upstream` (200), and a
successful non-stream real chat without restarting the Go gateway. The
offline three-stage HTTP-health reviewer also passed. **Do not repeat the
disruptive outage** just to run this optional follow-up.

This new operator command adds one **opt-in** short real streaming chat and
tests that the already running, *idle* Go gateway:

1. Reports healthy Ollama upstream and the correct **installed pinned digest**.
2. Exposes internally consistent inference (schema v3) and admission (schema
   v2) read-only metrics, with zero active attempts/requests at start.
3. Produces an actual nonempty SSE delta followed by exactly one standalone
   `data: [DONE]` terminal frame without later data.
4. Accounts for exactly **one** newly admitted and completed stream, with
   no new gateway-reported failures, rejections or quota admission timeouts.
5. Returns admitted active counts to zero, reports pinned model digest as
   actually resident in Ollama, and writes a **private sanitized** 0600 JSON
   report under `reports/local/`.

The script never stops Ollama or Go, resets metrics, changes a deployment,
downloads models, touches SQLite/Redis/PostgreSQL, or reads an operator token.
It sends one short prompt when explicitly executed, with up to 24 generated
tokens. **It does not assert that the model generated exactly the word READY**;
the criterion is a valid terminal SSE stream, not answer quality.

## Dry-run — zero network

```bash
cd ~/metriqen-llm-serving-optimization
python3 -m tools.demo.p5_stream_admission_smoke
```

## Execute on your original, idle loopback-only Mac runtime

The expected digest must come from a previously successful private P1 smoke
report of **the same exact model tag**. Never infer it from a movable model
tag alone and never post the full private JSON publicly.

```bash
cd ~/metriqen-llm-serving-optimization
umask 077

DIGEST="$(python3 - <<'PY'
from pathlib import Path
import json
import re
import stat

paths = list(Path("reports/local").glob("real-ollama-smoke-*.json"))
if not paths:
    raise SystemExit("BLOCKED: pinned P1 real Ollama report missing")
p = max(paths, key=lambda item: item.stat().st_mtime)
s = p.lstat()
if not stat.S_ISREG(s.st_mode) or s.st_mode & 0o077 or p.is_symlink():
    raise SystemExit("BLOCKED: report is not private regular evidence")
doc = json.loads(p.read_text())
if (doc.get("passed") is not True or doc.get("model_tag") != "llama3.2:1b"
        or not re.fullmatch("[0-9a-f]{64}", doc.get("model_digest", ""))):
    raise SystemExit("BLOCKED: pinned original digest not approved")
print(doc["model_digest"])
PY
)"

python3 -m tools.demo.p5_stream_admission_smoke \
  --execute \
  --model llama3.2:1b \
  --expected-digest "$DIGEST" \
  --output "reports/local/p5-stream-release-$(date +%Y%m%d-%H%M%S).json"
```

A successful run prints:

```text
P5 bounded post-recovery SSE and admission release: PASS
Exactly one verified SSE [DONE] and one admitted stream; active gateway slots returned to zero.
Private sanitized report written; no model output logged.
P5 cancellation, security and portfolio signoff: NOT YET COMPLETE
```

A running benchmark, other gateway client, model tag/digest change,
unavailable Ollama or malformed SSE returns **BLOCKED** instead of silently
certifying a result. Don't repeatedly retry if blocked; inspect service
health, the pinned digest and active workload first.

## Claims this does **not** establish

- No client-disconnect **cancellation** was induced or measured.
- Gateway handler/admission counters are process-local; this is not a
  distributed lease or cross-project quota validation.
- A clean one-stream terminal path is not proof that the earlier HTTP 502
  error body was redacted; that requires separate bounded negative-path
  checks and error-body review.
- Does not test OIDC identity, tenant RBAC, PostgreSQL/Redis integration,
  browser/mobile accessibility, P2's interrupted AC-power A/B/A/B evidence,
  P3 code-speed improvement, P5 full security or P6 portfolio release.
- No p95, TTFT, throughput, quality, GPU hardware execution or production
  uptime inference from this smoke.
- CI unit tests for this tool inject synthetic fixtures and must never be
  reported as Mac streaming acceptance.

The existing [P5 outage/read-only reviewer](p5-readonly-recovery-evidence.md)
and [final Mac handoff](portfolio-final-mac-handoff.md) remain the primary
operator guidance. All private reports stay Git-ignored.
