# A/B/A reversion study — Ollama inference parallelism

**Status:** the operator has reported a completed **historical A1 → B → later A2** sequence and generated a local descriptive A/B/A report on 2026-10-13. The A2 numeric results have not been independently inspected or published. **This protocol is still intended for future prospectively planned, uninterrupted replications; no causal effect or verified recovery is claimed.**
The [first operator-reported experiment](experiments/2026-10-13-ollama-parallelism.md)
observed a descriptive improvement for parallelism 2 under client concurrency 2,
but not under every load. An A/B/A (1 → 2 → 1) reversion tests whether the
original baseline can be recovered after the candidate run. It helps expose
thermal/caching/time drift, but it is **not** randomized causal identification.

## Scope and controls

- Keep the exact `llama3.2:1b` digest (check `/api/tags`), Ollama version,
  Q8_0 model, gateway, v1 dataset bytes, and macOS/hardware unchanged.
- One change only: `OLLAMA_NUM_PARALLEL=1` (A), `2` (B),
  `1` (A2). Record the active `n_seq_max` and `n_slots` in local
  server logs; the env command alone is not verification.
- For each phase, record start/end wall-clock time, `sw_vers`, charge/power
  status, available memory, background compute, room/fan conditions where
  observable, and model loading/caching state. The OS may manage temperature
  without exposing a reliable sensor; do not invent temperatures.
- Do **not** replay a failed experiment selectively to hide it, overwrite
  an old report directory, or commit private raw outputs.
- The older A1 and B reports can be examined, but because their timing,
  thermal and cache conditions were not prospectively randomized, a
  **new, uninterrupted A/B/A session** is preferable for a stronger study.

## Phase sequence

From repository root, check `git status --short` and
`git pull --ff-only origin main` only if the worktree is clean. Ollama and
the Go gateway must remain **loopback only**; do not run separate servers on
the same port.

To restart Ollama, stop the existing `ollama serve` process with Ctrl-C in
its own terminal. Start the correct single instance:

~~~sh
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=1 ollama serve
~~~

Change the `1` to `2` in the B phase, then back to `1` for A2. The
Go gateway can remain running. Perform one real request through
`http://127.0.0.1:8081/v1/chat/completions` after every model reload and
**confirm** the slot count in the Ollama console before collecting reports.

For each of **A1**, **B**, **A2**, create a unique new directory:

~~~sh
MODEL_DIGEST=$(curl -fsS http://127.0.0.1:11434/api/tags | python3 -c 'import json,sys; print(next((m["digest"] for m in json.load(sys.stdin)["models"] if m["name"]=="llama3.2:1b"), ""))')
test "${#MODEL_DIGEST}" -eq 64 || { echo "Exact model digest missing"; exit 1; }
RUN_DIR="reports/local/aba-A1-$(date +%Y%m%d-%H%M%S)"
OLLAMA_RUNTIME="$(ollama --version)"
python3 -m tools.benchmark.sweep \
  --model llama3.2:1b --model-revision "$MODEL_DIGEST" \
  --runtime "$OLLAMA_RUNTIME" --hardware "Apple M2 Metal" \
  --dataset docs/benchmarks/workload.v1.jsonl \
  --repeats 3 --requests 36 --warmup 2 \
  --max-tokens 128 --timeout 120 --output-dir "$RUN_DIR"
~~~

**Before executing the next phase**, change the `aba-A1` name to
`aba-B` or `aba-A2`, restart Ollama with the matching parallelism, and
confirm active slots. Preserve each phase's directory path, e.g. by writing
it in a private notebook. The shell variable `RUN_DIR` only persists in
the current Terminal session.

Following each completed nine-run sweep, run the **same reference evaluation**
and save it alongside the sweep:

~~~sh
python3 -m tools.nlp_eval.online \
  --model llama3.2:1b --model-revision "$MODEL_DIGEST" \
  --runtime "$OLLAMA_RUNTIME" --hardware "Apple M2 Metal" \
  --dataset docs/nlp-eval/workload.v1.jsonl \
  --max-tokens 128 --timeout 120 --output "$RUN_DIR/quality.json"
~~~

Inspect `ollama show llama3.2:1b` for quantization, confirm OS/hardware
details, review all run outcomes and then create the manifest:

~~~sh
python3 -m tools.benchmark.evidence_bundle \
  --sweep-dir "$RUN_DIR" --quality-report quality.json \
  --host-os "$(sw_vers -productName) $(sw_vers -productVersion)" \
  --quantization Q8_0 \
  --timestamp-utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --notes 'State phase, active slots, power, other workloads and caveats accurately' \
  --confirm-real-model-executed

python3 -m tools.benchmark.evidence_gate \
  "$RUN_DIR/evidence-manifest.json" --verify-reports
~~~

The flag is a human attestation only, **not proof** that the runtime or
hardware matched the claims. An aborted sweep is not a completed result.

## Three comparisons, not one winning number

After all three verified phases, retain the three exact paths as
`A1_DIR`, `B_DIR` and `A2_DIR`. Produce **new output filenames** for:

1. `A1 → B` — attempted performance change under the two-slot candidate.
2. `B → A2` — whether returning to one slot reverses the observation.
3. `A1 → A2` — time/thermal/cache drift while the setting is nominally the
   same.

The existing `tools.benchmark.compare` reads two complete bundles, so it
can be run separately for each pairing:

~~~sh
python3 -m tools.benchmark.compare \
  --baseline "$A1_DIR/evidence-manifest.json" \
  --candidate "$B_DIR/evidence-manifest.json" \
  --change 'A1 parallelism 1 versus B parallelism 2' \
  --output "reports/local/aba-A1-to-B-$(date +%Y%m%d-%H%M%S).json"

python3 -m tools.benchmark.compare \
  --baseline "$A1_DIR/evidence-manifest.json" \
  --candidate "$A2_DIR/evidence-manifest.json" \
  --change 'A1 versus A2 same setting; test temporal drift' \
  --output "reports/local/aba-A1-to-A2-$(date +%Y%m%d-%H%M%S).json"
~~~

Optionally compare `B_DIR` against `A2_DIR` for the reversal.
The comparator's `descriptive_not_causal` output can apply to the
unchanged-setting A1/A2 drift check too; do **not** interpret that label
as proof an optimization occurred.

Interpret every concurrency independently. Prefer a candidate only when
a repeatable improvement is meaningful for the target load, quality has
not visibly regressed, and the A1/A2 drift is small relative to the
candidate effect. Avoid declaring arbitrary percentage thresholds or
statistical significance without a predeclared analysis. Report the
single-thread TTFT/throughput regressions too.

## Automatic three-phase descriptive analysis

After all three phases have their own quality report and verified evidence manifest, use the [A/B/A analysis CLI](aba-analysis.md) to revalidate all raw data and consolidate the A1→B, B→A2 and A1→A2 drift metrics in one local JSON report. This CLI does not verify slot-count logs, phase timing or causal attribution; do not replace the operator's environmental notes with the automated classification.

## Evidence boundaries

Three repeated run summaries per concurrency per phase do not provide
a reliable confidence interval for population p95. An A/B/A design
reduces some alternative explanations but does not isolate memory use,
OS effects, streaming scheduling or cache interactions. Active slots and
device identity require human review. This is local Ollama/Metal
configuration research, **not** a new Metriqen batching algorithm or
a vLLM/GPU production result.
