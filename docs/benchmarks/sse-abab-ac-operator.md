# P3: AC-powered SSE relay buffer A/B/A/B (operator controlled)

**Status:** script and synthetic CI are ready, but **no real Mac A/B/A/B
measurements have been observed or approved by this document**.
The candidate is **1 KiB (default)** versus **4 KiB opt-in** read chunks
in Go's existing local SSE gateway. This does not change Ollama model
compute, quantization, GPU acceleration or batching.

## What the tool does — and does not do

`python3 -m tools.benchmark.sse_abab` has three subcommands:

- `plan` performs no network I/O or writes.
- `collect --execute` measures **one manually selected existing
  gateway mode** at concurrency 1/2/4 with the same streaming workload,
  12 measured requests at each level by default and one warmup per level.
- `review` reads all four sanitized private files **offline** and
  prints RPS, p95 TTFT, p95 latency and all failed request counts for
  each level and A1/B1/A2/B2. This report **always marks** actual
  speedup, P3 approval, semantic quality and hardware attestation as
  unverified. It does not manufacture a percentage improvement.

`collect` fails closed if AC power is not verified, the exact
`llama3.2:1b` installed digest differs, the gateway upstream is not
healthy, the listener is missing/duplicated, its actual executable is
not **exactly** the owner-built `reports/local/sse-abab-gateway`, its
listener is not bound to `127.0.0.1:8081`, or its process env setting
differs from the A/B phase. A macOS `lsof` text-vnode check and SHA-256
of that executable are required before/between/after measured loads.
**Four-phase review rejects unverified executables and different binary
hashes across phases.** It never logs full `ps eww` output, which can
contain credentials. Missing `METRIQEN_SSE_RELAY_BUFFER_BYTES` is
accepted as 1 KiB **only on the expected executable**.

These checks still do not independently attest the original Git source
SHA, in-memory mapped binary bytes, uninterrupted power or Ollama
worker slots. The operator must independently inspect source revision,
runtime slots and thermals.

No script action changes gateway settings, terminates a process,
installs a model, downloads weights, provisions a GPU, or sends
requests to non-loopback hosts. **Switches between A/B must be
performed manually by the owner, after saving each phase.**

## 1. Prepare Mac and verify real model identity

```bash
cd ~/metriqen-llm-serving-optimization
source .venv/bin/activate
git status --short --branch
git rev-parse HEAD
pmset -g batt
python3 -m tools.benchmark.sse_abab plan

MODEL_DIGEST="$(curl -fsS http://127.0.0.1:11434/api/tags | python3 -c '
import json,sys
items=json.load(sys.stdin)["models"]
found=[m["digest"] for m in items if m.get("name")=="llama3.2:1b"]
assert len(found)==1 and len(found[0])==64, "model missing or malformed digest"
print(found[0])
')"
printf 'Pinned model digest: %s\n' "$MODEL_DIGEST"
```

Verify that power output actually reports `AC Power`. Verify model
license, currently loaded Ollama and configured `OLLAMA_NUM_PARALLEL`
from actual process inspection. Do not infer model worker slots from
gateway's four-request admission cap. If a guarded Go Gateway is in
use, **do not disable existing credentials** or use this local unguarded
experiment without a separately approved isolated dev environment.

## 2. Compile one exact Go binary; leave control API and Ollama alone

```bash
mkdir -p reports/local
chmod 700 reports/local
cd services/inference-gateway
go test ./internal/httpapi -run 'TestSSERelayCandidateOptInPreservesLegacyDefault|TestStreamingRelayLargerReadBufferPreservesHTTPAndAccounting' -count=1
go build -o ../../reports/local/sse-abab-gateway ./cmd/server
cd ../..
```

Verify the immutable experiment executable and record the fingerprint locally:

```bash
git rev-parse HEAD
shasum -a 256 reports/local/sse-abab-gateway
lsof -nP -iTCP:8081 -sTCP:LISTEN
```

If port 8081 is occupied, inspect the *current* listener, not an
old recorded PID. Confirm process ownership and path with
`ps -p <verified-PID> -o pid,ppid,command` and
`lsof -nP -a -p <verified-PID> -d txt` (substitute the actual
numeric PID). Stop only the confirmed owner-controlled local gateway
through its foreground terminal/supervisor. Never kill an unknown
process or alter Ollama, FastAPI, credentials, or project auth. The
collector validates the active executable and loopback socket again.

**If it is the existing
owner-controlled Go development server**, terminate that specific
process gracefully using its owning terminal/process supervisor
and confirm the port is free. **Do not copy commands that kill an
unrecognized process or a shared production service.** Leave Ollama
11434 and FastAPI 8000 running; do not change their settings.
`scripts/start-local-demo.sh` REUSES an already running Go process
and strips arbitrary environment variables on launch: **it will not
switch this experiment's 1 KiB/4 KiB mode**.

## Important: reject a stale `go run` A1 baseline

On October 23 the operator compiled `reports/local/sse-abab-gateway`
but the attempt to start it failed with `bind: address already in use`.
The old PID 75718 was observed executing a `Library/Caches/go-build/`
binary, and **that old process served the subsequently recorded
36/36 A1 requests**. This is useful smoke evidence, but **NOT**
a valid 1 KiB same-binary A1 baseline and must not be included in
a final B1/A2/B2 review.

Before a new A1, inspect the current listener PID and its parent:

```bash
lsof -nP -iTCP:8081 -sTCP:LISTEN
ps -p 75713,75718 -o pid,ppid,command
```

Those historic PIDs are examples **not authoritative current targets**.
Stop the original foreground `go run` process with Ctrl+C in its
own terminal; if not possible, independently identify its current
owner/process and gracefully stop **only that verified local service**.
Confirm port 8081 is actually free before starting the new binary.
Keep the old private A1 report unchanged for audit, but select a **fresh
run name/output path** for every replacement phase.

The updated collector now refuses to run while a `go run` cache
binary owns port 8081. A historical A1 report lacking a verified
executable fingerprint is refused by the four-phase offline review.

## 3. Four sequential phases

Use **two Terminal windows**. In *Terminal A*, start the newly compiled
binary in the foreground, bound only to 127.0.0.1.

For A1 (**default 1 KiB**):

```bash
cd ~/metriqen-llm-serving-optimization
env -i PATH="$PATH" HOME="$HOME" TMPDIR=/tmp \
  METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 \
  METRIQEN_ALLOWED_MODELS=llama3.2:1b METRIQEN_QUEUE_DEPTH=0 \
  ./reports/local/sse-abab-gateway
```

In *Terminal B* (Mac `.venv` activated), keep one unique shared
directory name for the full four-phase protocol:

```bash
cd ~/metriqen-llm-serving-optimization
source .venv/bin/activate
RUN_NAME="sse-abab-$(date +%Y%m%d-%H%M%S)"
python3 -m tools.benchmark.sse_abab collect \
  --execute --phase A1 --expected-digest "$MODEL_DIGEST" \
  --requests 12 --max-tokens 64 \
  --output "reports/local/${RUN_NAME}-A1.json"
```

If `MODEL_DIGEST` was assigned in another terminal, repeat the pinned
digest extraction in Terminal B; environment variables do not carry
across independent Terminal windows.

When A1 finishes successfully, **Ctrl+C** the dedicated experiment
gateway in Terminal A, wait for 8081 to be free, then start B1 with
the **same binary** and the single explicit 4 KiB setting:

```bash
env -i PATH="$PATH" HOME="$HOME" TMPDIR=/tmp \
  METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 \
  METRIQEN_ALLOWED_MODELS=llama3.2:1b METRIQEN_QUEUE_DEPTH=0 \
  METRIQEN_SSE_RELAY_BUFFER_BYTES=4096 \
  ./reports/local/sse-abab-gateway
```

Then in Terminal B run `collect --phase B1` with the same digest,
request count and `--output "reports/local/${RUN_NAME}-B1.json"`.
Stop the dedicated experiment server, start **A2** with the A1
**default** command (no SSE env setting), collect the `A2` report.
Finally switch back to the **4 KiB** command for **B2** and collect
the `B2` report. Keep order exactly **A1 → B1 → A2 → B2**; no
concurrent phases and no model/queue/auth configuration changes.
The collector cannot start/restart services or infer undocumented
upstream concurrency settings.

A phase uses **39 total model calls** (36 measured + 3 warmups) by
default; a four-phase diagnostic uses 156 model calls. There is no
claimed completion time. If any phase errors or the Mac overheats,
stop and preserve valid private files without pretending four phases
finished. You may choose 24 or 36 requests per level for more samples,
but choose before A1 and keep it identical across phases.

## 4. Offline descriptive reviewer

After all four phase reports exist and remain owner-private:

```bash
python3 -m tools.benchmark.sse_abab review \
  --a1 "reports/local/${RUN_NAME}-A1.json" \
  --b1 "reports/local/${RUN_NAME}-B1.json" \
  --a2 "reports/local/${RUN_NAME}-A2.json" \
  --b2 "reports/local/${RUN_NAME}-B2.json"
```

Share **only sanitized aggregate** comparisons. Retain phase files
privately and check that they were produced in chronological order.
Interpret RPS, p95 TTFT, p95 latency and failures *together*, including
run-to-run A1–A2 and B1–B2 drift, thermal/slot changes and any missing
token coverage. **P3 requires additional matched 36-sample model
quality evaluation** and independent review of the real binary,
gateway process settings and offer/load fairness; the script deliberately
does not certify those facts.

If the candidate hurts tail latency, error rate or quality, or has no
repeatable meaningful benefit, restore the default 1 KiB mode and
report the negative result. A successful Go microbenchmark or a
successful A/B/A/B command alone **cannot** justify an LLM inference
speedup claim.

See the [safety hypothesis](sse-relay-buffer-candidate.md),
[portfolio P1–P6 gates](../PORTFOLIO_V1_ACCEPTANCE.md) and
[portfolio issue #123](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/123).
