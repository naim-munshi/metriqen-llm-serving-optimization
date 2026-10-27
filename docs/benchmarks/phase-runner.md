# Portfolio P2: guarded one-phase Mac runner

This helper reduces repetitive operator commands. It **does not** switch
`OLLAMA_NUM_PARALLEL`, restart Ollama/Gateway, unload models, probe actual
`n_slots`, invent measurements, or automatically declare P2/P3 complete.
It is only for a real developer-operated localhost Mac, with the pinned
`llama3.2:1b` revision and fixed v1 datasets.

## Preflight, four phases, and evidence provenance

Run in the **strict prospective order** A1 → B1 → A2 → B2. For A1/A2,
you must have started **exactly one** Ollama server with the actual
`OLLAMA_NUM_PARALLEL=1` process environment; for B1/B2 use `=2`.
If macOS's Ollama.app instance hides the environment, preflight **blocks**.
Do not substitute an assumed default or edit JSON to fabricate a pass.
Check model-load logs independently for `n_seq_max` and `n_slots`.

Plug in AC power first, keep power/thermal/background workloads stable,
and ensure the Go gateway and local Ollama respond on 127.0.0.1. Avoid
other interactive inference during the experiment.

Each `collect` command:
1. Takes a read-only local macOS power/model-digest/Ollama-version/
   process-parallelism/Gateway preflight and saves it as private
   `preflight.json`. If blocked, **no inference happens**.
2. Runs 9 chronological benchmark runs: 3 repeats × concurrency 1/2/4,
   36 measured requests per run (plus 2 warmups) with fixed workload,
   streaming and max_tokens=128.
3. Runs a separate nonstream 36-query reference-quality evaluation
   using the same pinned digest/runtime/hardware and max_tokens=128.
4. Keeps partial raw files on error, and **does not** make an
   evidence manifest or claim a successful phase if any step fails.

No phase names, paths, or reports are reused. Nothing is sent to
GitHub; outputs stay in ignored `reports/local/` under new private
directories. The entire phase can take considerable time on Apple M2.

### AC power monitoring during a new phase

The collector now also samples `pmset -g batt` at the start, every five
seconds during the nine sweeps and quality evaluation, and once more on
exit. It writes a private, non-overwriting `power-monitor.json` into
the phase directory, including when a run fails. Any sampled Battery or
unknown power state **blocks collection success** and prevents
`finalize` from approving its evidence manifest. Keep the entire
partial directory and do not use it as a clean A/B/A/B phase.

The monitor is observational and cannot prevent a disconnected charger
or prove uninterrupted AC between samples. Compare timestamped macOS
power-history events as well; independently review runtime model slots
and thermal/background workload stability. Original phases from older
versions may lack this file: that is **missing power evidence**, not proof
of continuous AC. Never retroactively fill in `power-monitor.json`
with invented samples. Existing private historical evidence is not
modified by this change.

### Phase A1 example (on Mac, not in CI)

First record the model digest independently:

~~~sh
cd ~/metriqen-llm-serving-optimization
MODEL_DIGEST=$(curl -fsS http://127.0.0.1:11434/api/tags |
  python3 -c 'import json,sys; m=[x["digest"] for x in json.load(sys.stdin)["models"] if x["name"]=="llama3.2:1b"]; assert len(m)==1; print(m[0])')
test "$(printf '%s' "$MODEL_DIGEST" | wc -c | tr -d ' ')" = 64 ||
  { echo "Model digest must be exactly 64 characters"; exit 1; }
~~~

**Only after** verifying that Ollama process environment is actually
`OLLAMA_NUM_PARALLEL=1` and its real model-load logs report one slot:

~~~sh
A1_DIR="reports/local/abab-A1-$(date +%Y%m%d-%H%M%S)"
python3 -m tools.benchmark.phase_run collect \
  --phase A1 --expected-model-digest "$MODEL_DIGEST" \
  --hardware "Apple M2 Metal" --output-dir "$A1_DIR"
~~~

After `collect` succeeds, inspect sweep, actual power/thermal notes,
Ollama's quantization (`ollama show llama3.2:1b`) and runtime slot logs.
Only then explicitly attest **your own** observation:

~~~sh
python3 -m tools.benchmark.phase_run finalize \
  --phase A1 --output-dir "$A1_DIR" \
  --quantization "Q8_0" \
  --host-os "$(sw_vers -productName) $(sw_vers -productVersion)" \
  --notes "Operator confirmed AC power, Ollama 1 active slot, background workload; record actual limitations" \
  --confirm-real-model-executed
~~~

`Q8_0` and the note are examples; they must match what the **actual**
operator observed. Human attestation is not cryptographic GPU evidence.
The finalizer strictly validates the completed raw run reports and 36
quality samples via the pre-existing evidence-bundle and evidence gate.

For B1 repeat with `--phase B1` and a **new** `B1_DIR` only
after manually stopping the prior Ollama service safely, configuring
`OLLAMA_NUM_PARALLEL=2`, and checking `n_seq_max=2` / `n_slots=2`
in the real runtime logs. Similarly A2 (`=1`) and B2 (`=2`), each
with a fresh private phase directory and preflight. Do not restart
or swap versions *within* one phase. Ollama.app and a standalone
`ollama serve` must never both own the same port.

### After all four phases

The helper creates per-phase paths:

~~~text
reports/local/abab-A1-.../
  preflight.json
  sweep/
    repeat-01-c1.json ... (9 individual run files)
    sweep-summary.json
    quality.json
    evidence-manifest.json
~~~

Repeat with B1/A2/B2. Once all four have been collected and explicitly
finalized, review the entire set with **one offline command**:

~~~sh
python3 -m tools.benchmark.four_phase_review \
  --a1-dir "$A1_DIR" --b1-dir "$B1_DIR" \
  --a2-dir "$A2_DIR" --b2-dir "$B2_DIR" \
  --output-dir "reports/local/abab-final-$(date +%Y%m%d-%H%M%S)"
~~~

Each `*_DIR` variable must refer to the unique phase directory from
its own successful collection/finalization (shell variables must be
restored in a new Terminal session). The command validates all nine
raw runs/phase, quality reports, four fresh preflights, environment
consistency and recorded chronology **before** making any output. It
writes private owner-only `abab-preflight-binding.json`,
`abab-analysis.json` and `abab-review.md` without overwriting
anything. This is descriptive internal consistency, **not a Mac/GPU
attestation, population confidence interval or causal improvement**.

The underlying [phase-preflight binding](phase-preflight-binding.md) and
[A/B/A/B review](abab-replication.md) are still supported separately.
No phase automatically marks A/B/A/B PASS.

**Caveats:** The binder checks the chronological and bounded **preflight
timestamp** (at most 15 minutes before the first run), model/version/digest,
phase order and raw evidence coherence. It does not independently inspect
actual runtime slots, sensor readings, true model execution or battery
conditions after the snapshot. Three repeated runs are too few for a
stable population-p95 confidence interval. A/B/A/B of Ollama parallelism
is a **configuration study**, not P3 Metriqen-controlled optimization.

### Troubleshooting

- `preflight_blocked_check_private_preflight_json`: stop; check AC, the
  installed tag/digest and visible *running Ollama* process environment.
  Never modify the recorded JSON to pass.
- `sweep_failed_partial_reports_retained`: preserve partial reports,
  correct runtime behavior and create a **new** phase directory; do not
  cherry-pick successful individual runs.
- `quality_failed_partial_reports_retained`: do not declare a complete
  phase. Correct the runtime and recollect with a new directory.
- `evidence_bundle_failed` / `strict_evidence_gate_failed`:
  inspect the private raw reports and stated metadata. Do not re-label a
  different model, digest, runtime, hardware or quantization to pass.

Use [A/B/A/B protocol](abab-replication.md) and
[Portfolio v1.0 P1–P6 acceptance](../PORTFOLIO_V1_ACCEPTANCE.md) for
all other release obligations.
