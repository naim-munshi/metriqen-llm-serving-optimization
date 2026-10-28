# P5 opt-in real Mac SSE client cancellation and recovered admission

This **separate, non-disruptive acceptance step** follows the already
verified real operator sequence: graceful Ollama outage → Go gateway
`/health/upstream` HTTP503 and bounded failed chat HTTP502 → Ollama restored
→ Go unchanged PID and valid nonstream response → offline 3-report health
review PASS → bounded SSE `[DONE]` and admitted-slot release PASS.

It tests **only** a different failure mode: an HTTP client disconnecting
during an active, streaming completion. Do **not** stop Ollama again or
restart Go/FastAPI for this step.

## Preconditions

- Local Mac gateway `127.0.0.1:8081`, native Ollama
  `127.0.0.1:11434`, and the **same pinned model digest** are available.
- Gateway is idle, with no other chat/benchmark clients. The Go gateway must
  remain loopback-only. Its current developer config must allow the model
  and must not require a bearer that this unauthenticated local test lacks.
- Running this sends a real, fixed prompt (up to 256 model output tokens)
  as SSE and **deliberately closes that client connection after the first
  real text delta**, then runs one 16-token-capped non-stream recovery chat.
  It does not modify local approved model tags, start/stop processes,
  manage project leases, reconfigure the model, read user credentials or
  change SQLite data.

## Zero-I/O plan

```sh
cd ~/metriqen-llm-serving-optimization
python3 -m tools.demo.p5_stream_cancel_recovery
```

## Actual Mac operator run (only when otherwise idle)

Use the same private pinned baseline digest extraction procedure already
documented in [P5 post-recovery SSE](p5-post-recovery-stream.md).
Never type a guessed/model-tag-derived digest.

```sh
cd ~/metriqen-llm-serving-optimization
umask 077

# Obtain the pinned, exact 64-char digest from the owner's previously
# verified real-smoke JSON. This example exits if it is missing/unsafe.
DIGEST="$(python3 - <<'PY'
import json, re, stat
from pathlib import Path

files = list(Path("reports/local").glob("real-ollama-smoke-*.json"))
if not files:
    raise SystemExit("BLOCKED: no private pinned real smoke")
path = max(files, key=lambda p: p.stat().st_mtime)
info = path.lstat()
if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
    raise SystemExit("BLOCKED: private evidence has unsafe permissions")
doc = json.loads(path.read_text())
digest = doc.get("model_digest", "")
if (doc.get("passed") is not True or doc.get("model_tag") != "llama3.2:1b"
        or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
    raise SystemExit("BLOCKED: wrong model / invalid pinned digest")
print(digest)
PY
)" || exit 2

python3 -m tools.demo.p5_stream_cancel_recovery \
  --execute --model llama3.2:1b --expected-digest "$DIGEST" \
  --output "reports/local/p5-cancel-recovery-$(date +%Y%m%d-%H%M%S).json"
```

A successful source run prints:

```text
P5 real single-stream client-cancel and resumed JSON inference: PASS
Exact gateway cancel + completion counters and released admission slots verified.
Private report saved without response content; full P5/security/release still pending.
```

A **BLOCKED** result is legitimate evidence of a model-eviction,
unexpectedly busy gateway, no observed cancellation, early model completion,
an unexpected counter delta, or an unavailable runtime; do not change
history or repeatedly send requests to force a PASS. If necessary, inspect
the private environment and re-run **once with a fresh report filename**
only after addressing the explicit cause. Runtime tests can sometimes
complete a fast model before its first delta is read; no model speed claim
can be inferred either way.

## Important limits

The tool looks for **one** process-local `client_canceled_total` increment,
one admitted stream cancellation, zero residual active/waiting requests,
and a later single admitted/complete JSON chat without new failures.
It explicitly validates the inference/admission metric schemas and
counter invariants. This demonstrates local gateway handling of a client
disconnect, not an upstream Ollama process termination attestation.
Go HTTP counters are in-memory and reset on process restart.

The report is a bounded, sanitized owner-private `0600` JSON in the
Git-ignored `reports/local/`, containing **no prompt/model output**
and only a short model digest prefix. The fixture-only CI tests do not
count as real Mac cancellation evidence.

This does **not** verify the prior outage's 502 response *body* is sanitized,
OIDC/Redis/PostgreSQL project isolation, distributed lease cleanup,
a real mobile Safari layout, AC-stable A/B/A/B, P3 causal speedup,
or P5/P6 release approval. For those separate obligations see
[Portfolio acceptance](../PORTFOLIO_V1_ACCEPTANCE.md).
