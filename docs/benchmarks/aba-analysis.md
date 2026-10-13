# Offline A/B/A reversion analysis CLI

The `tools.benchmark.aba_analysis` command creates a **private,
descriptive-only** JSON summary from three separately verified local
experiment folders. It does not run inference, authenticate hardware,
verify real slot counts, or prove causality.

The intended phase order is **A1 (OLLAMA_NUM_PARALLEL=1) → B (2) →
A2 (1)**. The tool cannot verify that phase order or setting from
self-reported manifests. Operators must inspect the model-load logs
(`n_seq_max`, `n_slots`) and record timestamps, thermal conditions,
power status, background processes and model loading state.

## Inputs

For **each** phase, collect the same repeat sweep and quality dataset,
then create and verify its human-attested `evidence-manifest.json`.
Read the [A/B/A runbook](aba-reversion-protocol.md) before running
a new phase. Keep phase directories private and distinct.

Once A2 is complete and all three phases were verified:

~~~sh
A1_DIR=reports/local/v1-baseline-20261013-104946
B_DIR=reports/local/v1-parallel2-20261013-120425
A2_DIR=reports/local/aba-A2-REPLACE_WITH_ACTUAL_TIMESTAMP

python3 -m tools.benchmark.aba_analysis \
  --a1 "$A1_DIR/evidence-manifest.json" \
  --b "$B_DIR/evidence-manifest.json" \
  --a2 "$A2_DIR/evidence-manifest.json" \
  --output "reports/local/aba-analysis-$(date +%Y%m%d-%H%M%S).json"
~~~

**Important:** These example A1 and B paths refer to the developer's
previous, **non-prospectively controlled** session. Adding a later A2
does not transform that study into an uninterrupted, randomized or
causally identified experiment. For a stronger repeat, collect three
**new** phases in one planned session (unique new folders).

## What is checked

The CLI invokes the existing strict two-phase comparator three times.
Each comparison rechecks the local manifest, each raw request
observation, report p50/p95 and TTFT, all nine run identities, the
sweep aggregate, model digest, OS/hardware labels, quantization, gateway,
workload hashes, decoding settings and exact/normalized reference
quality. It aborts on missing files, inconsistent inputs, nonmatching
datasets, failed requests, invalid summaries or duplicate phase paths.

The tool generates no artificial model response, thermal measurement or
token count, and it refuses to overwrite any existing output.

## Output semantics

Per client concurrency (1, 2 and 4), and for throughput, latency
p50/p95 and TTFT p50/p95, the report shows:

- Median across repeated A1, B and A2 measurements.
- Repeat-run **minimum and maximum** of each of the six metrics across all
  three phases (computed from the original validated run summaries).
  These are run-level descriptive ranges, not confidence intervals,
  pooled per-request statistics or proof of an effect.
- **A1 → B** percent change: original candidate observation.
- **B → A2** percent change: descriptive reversal observation.
- **A1 → A2 drift** percent change: original vs returned configuration.
- **B vs midpoint of A1 and A2 medians** percent change: purely descriptive
  context. **This midpoint is not a validated causal counterfactual.**

Percent change is `(later / earlier - 1) × 100`; positive is favorable
for throughput, negative is favorable for latency or TTFT. If the
denominator is zero, the tool records `null` rather than inventing a
percentage. Missing completion-token usage stays `null` across outputs.

Status names: `quality_regression_requires_review`,
`exploratory_small_sample`, `descriptive_missing_token_usage`, or
`descriptive_reversion_observations_only`. Even the last status **does
not** mean the reversion succeeded or that a causal speedup was proven.

No automatic stability cutoff is imposed. The analyst must compare the
size of A1/A2 drift with the candidate effect, inspect run-to-run
variation and nonstreaming reference scores, and disclose the time and
environmental changes. Three runs per concurrency are not enough to
treat median-p95 differences as statistically significant.

This is a local read-only evidence analysis until the final **new**
aggregate JSON is written. Never commit private `reports/local/`,
raw model responses or private operational logs.


## Operator-reported A2 completion (2026-10-13)

The developer reported nine successful 36-request A2 benchmark runs in
`reports/local/aba-A2-20261013-145925`, evaluated 36 reference questions,
created a human-attested local evidence manifest, and ran this tool.
The generated private summary path was
`reports/local/aba-analysis-20261013-153817.json` with the classification
`descriptive_reversion_observations_only`.

**This classification does not say the original baseline recovered.**
The actual A2 throughput, TTFT, p95 latency, A1-to-A2 drift and A2 quality
counts have not yet been inspected by the repository reviewer. Human notes
about the returned slot count and thermal/power conditions remain necessary.
The original public two-phase case study has not been silently rewritten
to include A2 values.

## Produce a private reviewer-friendly three-phase Markdown report

From the project root on the machine with all three complete private
evidence bundles, run:

~~~sh
python3 -m tools.benchmark.aba_review \
  --a1 reports/local/v1-baseline-20261013-104946/evidence-manifest.json \
  --b reports/local/v1-parallel2-20261013-120425/evidence-manifest.json \
  --a2 reports/local/aba-A2-20261013-145925/evidence-manifest.json \
  --output "reports/local/aba-review-$(date +%Y%m%d-%H%M%S).md"
~~~

The CLI independently invokes the strict A/B/A verifier on all three
source bundles and writes only aggregate per-concurrency medians, per-run
minimum/maximum ranges, percent changes, quality rates, identities and
explicit uncertainty/provenance disclaimers. It does not export prompts,
individual responses, local
server logs or complete raw measurements, and refuses to overwrite an
existing report. The resulting Markdown is **private under reports/local/**
until manually reviewed for accurate context and cleared for publication.
Do not commit it or claim causal speedup based only on the generated
table.

The original saved JSON A/B/A summary can also be read offline with
`python3 -m json.tool reports/local/aba-analysis-20261013-153817.json`.
The running Ollama server and gateway are not required for either
reporting command.
