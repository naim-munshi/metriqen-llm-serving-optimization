# Local FIFO admission overload comparison

**Status:** protocol + instrumentation; no benchmark result or optimization claim.

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

## Step-by-step local execution

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
