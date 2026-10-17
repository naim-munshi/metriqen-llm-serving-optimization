# A/B/A/B phase preflight-to-evidence binding

This is an **offline consistency gate** for a four-phase A1 → B1 → A2 → B2
Ollama experiment. It closes the gap between the separately collected
[macOS read-only preflight](preflight.md) snapshots and the already validated
[A/B/A/B raw evidence](abab-replication.md). It performs no inference, no
process changes, no local network requests and no runtime slot probes.

**It does not demonstrate that real Mac/GPU inference was executed or that
A/B/A/B caused a performance speedup.** All timestamps, environment labels and
phase assignment still originate from the human operator. In particular a
configured `OLLAMA_NUM_PARALLEL=2` can never attest live
`n_seq_max=2/n_slots=2`.

## 1. Record each phase independently

Select the intended model digest using an independent local check; never
pull/retag models in the middle of the comparison.

For A1 and A2, configure `OLLAMA_NUM_PARALLEL=1`; for B1 and B2
configure `OLLAMA_NUM_PARALLEL=2`. **Restart and verify the Ollama server
configuration**, then examine the actual model-load logs for n_seq_max and
n_slots. The preflight only checks the visible process environment and
does **not** verify actual inference slots.

Immediately before the **first benchmark run** of each phase, capture a
fresh `preflight.json` in the phase's private evidence directory:

```sh
# Example for A1 only; repeat independently for B1, A2 and B2.
python3 -m tools.benchmark.preflight \
  --expected-model-digest "$MODEL_DIGEST" \
  --expected-parallelism 1 \
  --output "$A1_DIR/preflight.json"
```

The gate requires that each preflight report is **successful, no more than
15 minutes before the first recorded run, and after the previous phase has
finished**. The preflight must confirm AC power, Darwin, one `ollama serve`
process, an accessible local gateway, the pinned installed model digest,
the same Ollama version reported by that phase's evidence, and the correct
visible parallelism.

A single preflight reused between phases is rejected, as is an expired
preflight, unmatched model digest, an unsupported/blocked report, a report
with `active_slots_verified=true`, duplicate JSON keys, a symlink, or a
file exceeding 64 KiB.

Run the existing [repeated sweep](repeated-sweep.md), local quality
evaluation, and [strict evidence bundle](real-model-evidence-bundle.md) for
the same phase **without changing the experimental profile**. Each sweep
must contain nine run reports with complete UTC run timings (three repeats
at concurrency 1/2/4), and pass the existing report-integrity comparator.
If a restart is required during a phase, begin an **entirely new phase**;
do not silently splice two different server processes into a sweep.

Do not simply copy forward an older baseline sweep. Legacy evidence without
recorded UTC run timings cannot prove preflight freshness and will be
**rejected** by this stricter gate.

## 2. Verify all four phases after collection

From the repository root:

```sh
python3 -m tools.benchmark.abab_preflight_gate \
  --a1 "$A1_DIR/evidence-manifest.json" \
  --a1-preflight "$A1_DIR/preflight.json" \
  --b1 "$B1_DIR/evidence-manifest.json" \
  --b1-preflight "$B1_DIR/preflight.json" \
  --a2 "$A2_DIR/evidence-manifest.json" \
  --a2-preflight "$A2_DIR/preflight.json" \
  --b2 "$B2_DIR/evidence-manifest.json" \
  --b2-preflight "$B2_DIR/preflight.json" \
  --output "reports/local/abab-preflight-consistency.json"
```

Exit status **0** means all four phase configurations and their timed
benchmark/quality evidence were **internally consistent**. Exit status
**2** blocks the comparison due to missing, malformed, stale, out-of-order,
inconsistent, or incomplete evidence. The output JSON is optional, written
with exclusive create (never overwrites), and contains only phase labels,
configuration consistency, reported parallelism and the time lag to the
phase's first recorded run. It does not reproduce model outputs, prompts,
user identities, process environments, secrets, or private filesystem paths.

After successful binding, run the descriptive comparison:

```sh
python3 -m tools.benchmark.abab_review \
  --a1 "$A1_DIR/evidence-manifest.json" \
  --b1 "$B1_DIR/evidence-manifest.json" \
  --a2 "$A2_DIR/evidence-manifest.json" \
  --b2 "$B2_DIR/evidence-manifest.json" \
  --output "reports/local/abab-review.md"
```

Preserve the four raw directories privately. Record in the human-readable
review the observed slot logs, background workload, power/thermals,
runtime and OS versions and whether real Ollama inference was manually
observed. The gate does not read or attest those observations.

## Evidence boundaries

- GitHub CI runs **synthetic fixtures on Linux**, not macOS benchmarks.
- The four reports, preflights and timestamps are all **operator-sourced**;
  a fabricated internally consistent bundle can still pass.
- Checking the configured Ollama process environment cannot prove the
  **runtime's active slots or actual model weights/digest at inference**.
- No clock signing, GPU telemetry, raw-power measurement, token-signature
  attestation, semantic quality guarantee or cross-run thermal control.
- A/B/A/B with three repeats/concurrency gives **descriptive** RPS, TTFT and
  latency deltas; it is not a causal or statistically powered conclusion.
- The legacy local unguarded gateway mode and all optional security guards
  remain separate operational concerns.

Do **not** publish performance claims based solely on this gate. Only
operator-reviewed, real-model raw reports can support a narrowly scoped
and carefully qualified local experiment.
