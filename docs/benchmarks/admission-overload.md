# Local FIFO admission overload comparison

**Status:** protocol + instrumentation, followed by an [October 21 operator-reported A/B descriptive result](experiments/2026-10-21-fifo-admission-overload.md). The private offline consistency reviewer passed, but candidate B's observed goodput was worse at every tested concurrency and formal P3 acceptance remains open. **No causal speedup or optimization claim.**

This runs against the **real loopback Go gateway** and locally operated Ollama.
Unlike the strict 1/2/4 sweep, a 429 rejection is an EXPECTED observation,
not a reason to discard the run. Every outcome remains in the denominator.
It cannot prove batching speedups or infer model quality.

## Compare two gateway policies

| Variable | A: baseline | B: FIFO candidate |
| --- | --- | --- |
| METRIQEN_QUEUE_DEPTH | 0 | 2 |
| Active gateway cap | 4 | 4 |
| Model digest/quantization | Same verified artifact | Same verified artifact |
| Ollama slots | Same setting (e.g., 1) | Same setting (e.g., 1) |
| Streaming workload | docs/benchmarks/workload.v1.jsonl | Identical dataset bytes |
| Offered concurrency | 4, 6, 8, 12 | 4, 6, 8, 12 |
| Per-phase default | 3 repeats; 36 requests/level/run | Identical |
| Generation | Temperature 0; max 128 tokens | Identical |

Record AC-power status, memory pressure, background tasks, hardware/runtime,
model digest, and actual `n_seq_max` / `n_slots` logs **before each phase**.
Use a single Ollama process, and do not change Ollama parallelism between
conditions. Model revision/hardware passed on the CLI are operator supplied,
not cryptographically attested. Separately inspect Ollama logs and run the
existing `tools.benchmark.preflight` for each phase.

## Guarded A/B operator collection (recommended)

`python3 -m tools.benchmark.admission_phase` makes the protocol harder to
misconfigure but **does not** change Ollama/Gateway process settings, inspect
live n_slots, or authenticate model execution. In each phase it:

1. Checks macOS **AC power**, exactly one configured Ollama process, pinned
   installed model digest/version and Go gateway liveness **before inference**;
   saves the private `preflight.json` or blocks on failure.
2. Checks the live idle gateway's advertised **four active slots** and the
   requested Go queue depth (A: 0, B: 2) before sending any requests.
3. Collects 3 repeats × four concurrency levels (4/6/8/12) × 36 measured
   requests (**432 offered requests per phase**), preserving successes,
   HTTP 429s and all failed responses instead of discarding them.
4. Collects a separate **36-answer reference-quality report** and checks
   AC/model/process configuration again at the end (`postflight.json`).
   This cannot establish uninterrupted AC or thermal stability.

Keep both phases at the **same** manually verified Ollama parallelism, model
digest, decoding, hardware and background-load conditions. For example, if the
single existing Ollama server is already configured with two runtime slots
(`OLLAMA_NUM_PARALLEL=2` and `n_slots=2` in contemporaneous startup logs),
**leave it at 2 for both A and B**. If using 1, pass
`--expected-parallelism 1` in **both** commands. Do not infer active slots
from configured process environment or from this helper's PASS.

**Operator Terminal 1:** Keep one loopback Ollama server running. Inspect
fresh model-load logs showing actual `n_seq_max` and `n_slots`, power
status and model quantization before A, and again before B as appropriate.

**Operator Terminal 2:** Stop *only* the existing Go gateway gracefully
and start baseline A with the same upstream. Confirm there is no duplicate
gateway listening on `127.0.0.1:8081`:

~~~sh
cd ~/metriqen-llm-serving-optimization/services/inference-gateway
METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 METRIQEN_QUEUE_DEPTH=0 go run ./cmd/server
~~~

**Operator Terminal 3, after AC/slot verification:**

~~~sh
cd ~/metriqen-llm-serving-optimization
RUN_A="reports/local/admission-A-$(date +%Y%m%d-%H%M%S)"
python3 -m tools.benchmark.admission_phase \
  --phase A \
  --expected-model-digest baf6a787fdffd633537aa2eb51cfd54cb93ff08e28040095462bb63daf552878 \
  --expected-parallelism 2 \
  --hardware "Apple M2 Metal" \
  --output-dir "$RUN_A"
~~~

**Only after A is complete:** Stop and restart *only* the Go gateway with
`METRIQEN_QUEUE_DEPTH=2` and otherwise unchanged settings. Confirm the live
metrics endpoint now reports queue depth 2 and active cap 4. Repeat the
slot/power review. Run candidate B, using the **same parallelism** as A:

~~~sh
RUN_B="reports/local/admission-B-$(date +%Y%m%d-%H%M%S)"
python3 -m tools.benchmark.admission_phase \
  --phase B \
  --expected-model-digest baf6a787fdffd633537aa2eb51cfd54cb93ff08e28040095462bb63daf552878 \
  --expected-parallelism 2 \
  --hardware "Apple M2 Metal" \
  --output-dir "$RUN_B"
~~~

The digest, hardware label and parallelism above are **operator-specific
examples**, not asserted by the tool. Obtain the actual live digest and
check it against the pinned model. After both fully collected phases,
use the existing strict offline reviewer, pointing it to the **overload**
subdirectories (the helper also saves pre/postflight and quality at each
phase root):

~~~sh
python3 -m tools.benchmark.admission_tradeoff \
  --baseline-dir "$RUN_A/overload" \
  --candidate-dir "$RUN_B/overload" \
  --output "reports/local/admission-tradeoff-$(date +%Y%m%d-%H%M%S).json"
~~~

Keep a record of `RUN_A` and `RUN_B`: shell variables disappear if a
terminal closes. Inspect both `quality.json` reports for matched narrow
reference-score parity, the two pre/postflights, thermal notes and complete
HTTP 429/goodput/p95/TTFT matrix. The tradeoff reviewer **does not** ingest
the separate reference-quality files or automatically mark P3 complete.
Blocked or partial runs must not be passed off as completed evidence.

## Manual alternative

### Step-by-step local execution

**Terminal 1:** Run exactly one existing loopback Ollama process on
127.0.0.1:11434. If Ollama is fully stopped, you may use
`OLLAMA_NUM_PARALLEL=1 ollama serve`. Do not start a second process or
silently kill an instance. Verify its effective environment and model slots.

**Terminal 2:** Run the Go gateway with A's queue depth:

~~~sh
cd ~/metriqen-llm-serving-optimization/services/inference-gateway
METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 \
METRIQEN_QUEUE_DEPTH=0 \
go run ./cmd/server
~~~

After A completes, stop **only this gateway** and restart with
`METRIQEN_QUEUE_DEPTH=2` for B. Gateway metric totals reset when restarted;
the CLI records within-run differences, not raw process totals.

**Terminal 3:** From repository root, obtain your actual model digest
and confirm preflight. Inspect any blockers before benchmarking:

~~~sh
cd ~/metriqen-llm-serving-optimization
MODEL_DIGEST=$(curl -fsS http://127.0.0.1:11434/api/tags |
  python3 -c 'import json,sys; print(next((m["digest"] for m in json.load(sys.stdin)["models"] if m["name"]=="llama3.2:1b"), ""))')
test -n "$MODEL_DIGEST" && printf 'Digest: %s\n' "$MODEL_DIGEST"

python3 -m tools.benchmark.preflight \
  --expected-model-digest "$MODEL_DIGEST" --expected-parallelism 1 \
  --output "reports/local/admission-preflight-A-$(date +%Y%m%d-%H%M%S).json"
~~~

If digest retrieval fails or preflight blocks, stop here. A successful
preflight **still requires independent active-slot log review**.

First run a **small technical smoke**, not performance evidence:

~~~sh
python3 -m tools.benchmark.admission_load \
  --model-revision "$MODEL_DIGEST" --runtime ollama-0.30.8 \
  --hardware 'Apple M2 Metal, 8 GiB' --expected-queue-depth 0 \
  --repeats 1 --requests 12 --warmup 1 --max-tokens 64 \
  --output-dir "reports/local/admission-smoke-A-$(date +%Y%m%d-%H%M%S)"
~~~

When stable, run the intended **full A measurement**:

~~~sh
python3 -m tools.benchmark.admission_load \
  --model-revision "$MODEL_DIGEST" --runtime ollama-0.30.8 \
  --hardware 'Apple M2 Metal, 8 GiB' --expected-queue-depth 0 \
  --dataset docs/benchmarks/workload.v1.jsonl \
  --repeats 3 --requests 36 --warmup 2 --max-tokens 128 \
  --output-dir "reports/local/admission-A-$(date +%Y%m%d-%H%M%S)"
~~~

For B, confirm depth 2 in Terminal 2, repeat preflight (new filename)
and use **identical** CLI parameters except for
`--expected-queue-depth 2` and a fresh `admission-B-...` output path.
Replace the example hardware/runtime descriptions with real observations.
Use new directories for every phase; do not overwrite historical evidence.
Additional A/B/A/B sessions may illuminate temporal drift, but a fixed
sequence alone is not causal evidence.

## Offline P3 queue-policy tradeoff review (after real A and B phases)

This is a **Metriqen-owned FIFO admission policy comparison**, not the
Ollama `OLLAMA_NUM_PARALLEL` configuration study. After both
**complete** three-repetition 36-request/level A (queue depth 0) and B
(queue depth 2) runs exist, use the private strict offline reviewer:

~~~sh
python3 -m tools.benchmark.admission_tradeoff \
  --baseline-dir "reports/local/admission-A-YOUR_ACTUAL_RUN" \
  --candidate-dir "reports/local/admission-B-YOUR_ACTUAL_RUN" \
  --output "reports/local/admission-tradeoff-$(date +%Y%m%d-%H%M%S).json"
~~~

Replace the phase directories with real recorded paths. It independently
reads and validates **every raw request outcome**, HTTP 429 count,
chronological run identity, per-run gateway admission-counter deltas and
aggregate summary for all concurrency 4/6/8/12. It rejects differing
model digests, dataset fingerprints, runtime/hardware labels, decoding
profiles or any second changed experimental configuration. Its output
compares successful goodput, total successes, HTTP 429s, and successful-
request p95 latency/TTFT for **all four levels** and prominently exposes
adverse tradeoffs. A 429 does not count as a successful request and is
never quietly excluded from the acceptance-rate denominator. The output
stays under ignored `reports/local/` and does not copy prompts or model
responses.

**A descriptive queue tradeoff, even if positive, cannot automatically
satisfy P3.** The two phases are sequential and operator-sourced,
not randomized or hardware-attested. Compare real n_slots, power, thermals,
queue timeout/canceled counters, load-induced failures and separate matched
36-query reference-answer quality; show losses as well as gains. The
report unconditionally states `portfolio_p3_complete: false` and
`causal_speedup_verified: false`. If the policy is worse, publish a
qualified **negative result**, never an invented optimization percentage.

## Output semantics and limitations

Each `repeat-XX-cN.json` stores every request status, success/failure,
latency, streamed TTFT when available, and gateway admission-counter deltas,
without prompt or model response text. `admission-summary.json` includes:

- All offered requests, successes, failures and client-observed HTTP 429s.
- Successful completed requests/s (goodput) for each run and median by level.
- p95 latency/TTFT **among successful requests only**, not all offered traffic.
- Admitted-immediate/queued, rejected-full, queue-timeout and canceled deltas.

The gateway's per-run counter deltas include **warmup requests** and any other
traffic during the window; run without unrelated clients. A client HTTP 429
could originate in the gateway or Ollama, so distinguish it from
`rejected_full_total` and `queue_timeout_total`. Existing gateway snapshots
are process-local, reset on restart and require idle counters at each read.
The thread-pool's offered concurrency is **not** a controlled arrival rate
or synchronized burst. p95 on successes alone may improve artificially if
more slow requests fail: present failures and successful latency together.

A crash or invalid counters leaves partial per-run files but no completed
summary. The CLI does not authenticate hardware, actual Ollama slots,
power/thermal state, quality parity or a speedup. Further independently
reviewed performance/quality checks are required before portfolio claims.
All `reports/local/` artifacts remain Git-ignored; do not commit them.
