# Four-phase A/B/A/B descriptive replication

This is a **prospectively planned experimental protocol** for four
independently collected Ollama phases, in the declared A1, B1, A2, B2 order.

| Phase | Expected configured parallelism | Required independent log review |
| --- | ---: | --- |
| A1 | 1 | n_seq_max=1, n_slots=1 |
| B1 | 2 | n_seq_max=2, n_slots=2 |
| A2 | 1 | n_seq_max=1, n_slots=1 |
| B2 | 2 | n_seq_max=2, n_slots=2 |

The analysis tool does **not** set these variables, control model processes,
or authenticate actual slot counts. Preserve the pinned digest, quantization,
hardware, Ollama and gateway versions, datasets, decoding parameters,
stream-usage setting and request counts for all four phases.

Use the [existing A/B/A operator runbook](aba-reversion-protocol.md) as a
basis and repeat the same B settings for B2. Before each phase, capture
[read-only macOS preflight](preflight.md), model-load slot logs, battery/
power state, background load, timestamps and environment notes. Complete
a unique nine-run [sweep](repeated-sweep.md), the matched 36-question
reference quality evaluation and a strictly verified
[evidence bundle](real-model-evidence-bundle.md) before changing phases.
Do not exclude unusual repeats or overwrite old report directories.

**October 14 A1 caveat:** The initial A1 sweep was collected before the
new UTC per-run timing fields became available. The four-phase tools
accept that legacy evidence but cannot invent the missing timestamps.
For a fully prospectively documented timed design, start a fresh A1 after
updating the local runner and collect all four phases in the intended
order during a controlled session.

## Produce offline results

After A1, B1, A2 and B2 each have a complete verified
evidence-manifest.json (in distinct private directories), use:

~~~sh
python3 -m tools.benchmark.abab_review \
  --a1 "$A1_DIR/evidence-manifest.json" \
  --b1 "$B1_DIR/evidence-manifest.json" \
  --a2 "$A2_DIR/evidence-manifest.json" \
  --b2 "$B2_DIR/evidence-manifest.json" \
  --output "reports/local/abab-review-$(date +%Y%m%d-%H%M%S).md"
~~~

For a machine-readable summary, use the same four manifest arguments with
the companion module tools.benchmark.abab_analysis and a distinct JSON
output filename. Both modules are offline, perform no inference and only
export aggregate metrics/limited timestamps.

The analysis independently revalidates all raw evidence through the strict
comparator, then shows:
- A1 versus B1 and A2 versus B2: the two candidate effects.
- A1 versus A2 and B1 versus B2: same-setting temporal drift.
- Repeated-run min/max for throughput, latency and TTFT at concurrency 1,2,4.
- Narrow reference-quality string matching for each phase.
- Available UTC timestamps, with partial/legacy coverage explicitly labeled.

Chronology checks reject overlapping or reversed **recorded** times when
sufficient timing information exists. Local clock reports and operator
phase labels are not independently attested.

## Evidence limits

Even if both candidate effects have the same direction, a fixed A/B/A/B
sequence cannot establish causality or statistically significant speedups.
Caching, device scheduling, power management, temperature, model load state
and concurrent compute remain possible confounders. Three runs per
concurrency do not establish reliable population-p95 confidence intervals.
Similar response-token totals or exact-match scores do not establish
semantic quality parity, production readiness, or GPU optimization.
Report unfavorable as well as favorable results; this is a descriptive
study of local Ollama settings, not a Metriqen-developed scheduler.
