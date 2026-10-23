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
healthy, the listener is missing/duplicated, or the active gateway
process env setting differs from the requested A/B phase. It checks
process/PID, model and AC again between loads and after the phase.
It never prints or writes the full process environment: it may contain
secrets. **A mode without `METRIQEN_SSE_RELAY_BUFFER_BYTES` is
interpreted as the 1 KiB default but is NOT a binary/source SHA
attestation.** Power may fluctuate mid-request despite boundary checks.
The operator must independently check runtime slots, temperature and
the exact binary compiled from the expected source.

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

Inspect the current server with
`lsof -nP -iTCP:8081 -sTCP:LISTEN`. **If it is the existing
owner-controlled Go development server**, terminate that specific
process gracefully using its owning terminal/process supervisor
and confirm the port is free. **Do not copy commands that kill an
unrecognized process or a shared production service.** Leave Ollama
11434 and FastAPI 8000 running; do not change their settings.
`scripts/start-local-demo.sh` REUSES an already running Go process
and strips arbitrary environment variables on launch: **it will not
switch this experiment's 1 KiB/4 KiB mode**.

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
