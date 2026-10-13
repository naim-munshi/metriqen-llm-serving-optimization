# Offline repeat-run variability review

The read-only offline command below rechecks an existing local sweep's
raw per-run reports against the published sweep summary, then writes a
**private descriptive Markdown review**. It does not run a model, make
LLM inference requests, start a server, or modify experiment evidence.

From the repository root, using an existing completed sweep:

~~~sh
python3 -m tools.benchmark.run_review \
  --sweep-dir reports/local/v2-A1-20261014-012830 \
  --output reports/local/v2-A1-20261014-012830/run-variability-review.md
~~~

- Reads each run in the original rotating order for concurrency 1, 2 and 4.
- Revalidates run outcomes, workload digest, model digest, settings and
  aggregate statistics. When present, validates and cross-checks per-run
  UTC timing and chronological order against raw reports.
- Displays per-run successful requests/sec, TTFT p50, latency p95, measured
  wall time and total generated tokens, plus timestamp (if recorded).
- Computes maximum/minimum ratios across the three or more repetitions
  for RPS and TTFT p50. A ratio of **2x or greater** is flagged as
  *descriptive screening only*. This is a deliberately simple heuristic,
  not an outlier removal rule, confidence interval or statistical test.
- States whether completion-token totals are equal between repetitions.
  Equal totals do not prove identical cache or power conditions.
- Refuses to overwrite a pre-existing Markdown review. Existing raw
  benchmark files are never modified by this command.

**Legacy support:** sweeps from before UTC timing metadata was added
remain readable; timestamp columns explicitly say "Not recorded" rather
than inventing dates. New runs have the timestamps described in
[repeated sweep methodology](repeated-sweep.md).

Raw reports remain private under the Git-ignored reports/local directory.
The Markdown reviewer does not print local absolute paths, prompts or
generated completions. Do not interpret its flags as evidence that
OLLAMA_NUM_PARALLEL improved or degraded performance: that requires
separately controlled phases, matched model quality and independent
operator verification of active runtime slots.
