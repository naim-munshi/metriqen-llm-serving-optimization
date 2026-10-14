# NLP quality baseline — deterministic reference evaluation

This independent **offline** evaluation component complements the inference benchmark.
It does not judge semantic equivalence, hallucination, toxicity or model safety.

Input JSONL requires stable IDs:

References: `{"id":"q1","reference":"Paris"}`
Predictions: `{"id":"q1","prediction":"paris"}`

Run from repository root:

~~~sh
python -m tools.nlp_eval.evaluator \
  --references docs/nlp-eval/references.example.jsonl \
  --predictions docs/nlp-eval/predictions.example.jsonl \
  --output reports/local/quality-baseline.json
~~~

Measurements:

- Exact match compares unmodified Python strings.
- Normalized exact match applies Unicode NFC, Unicode casefold, whitespace collapse and trim. Punctuation is deliberately retained.
- Every reference ID must have exactly one prediction ID. Duplicate, missing and extraneous IDs cause a nonzero exit instead of silently inflating accuracy.
- Files have SHA-256 fingerprints. The JSON report includes counts and metric values only; it contains neither answers nor prompts.
- Do not compare optimization configurations without pinning the dataset, model revision, decoding parameters and scoring version.
- This is appropriate for narrow, factoid or deterministic answer tasks. Free-form generation needs carefully validated semantic evaluation and human review before quality claims.

Run tests with `python -m unittest discover -s tools/nlp_eval/tests -v`.

Future steps: standardized NLP datasets with licensing review, tokenizer-aware workload buckets,
semantic-similarity metrics (versioned embedding model), model-based evaluation with calibration,
and reproducible quality-vs-latency comparison against real vLLM/GPU inference.


## Local-only inference-to-quality workflow

Prepare a local workload JSONL with one record per line. Each record has
an id, messages array and reference answer. Example:

~~~json
{"id":"q1","messages":[{"role":"user","content":"Name the capital of France. Answer with one word."}],"reference":"Paris"}
~~~

Start local Ollama and the loopback Go gateway per the local inference guide.
Then run from repository root:

~~~sh
python -m tools.nlp_eval.online \
  --model llama3.2:1b \
  --model-revision llama3.2:1b \
  --runtime "Ollama: record actual version" \
  --hardware "Record actual hardware" \
  --dataset docs/nlp-eval/workload.example.jsonl \
  --max-tokens 64 \
  --output reports/local/quality-online.json
~~~

The tool sends sequential nonstreaming requests to the fixed localhost
inference endpoint and applies exact/normalized metrics to in-memory model
answers. It writes only aggregate metrics, the dataset hash, self-reported
environment, decoding settings and client-observed latency percentiles.
A failed response aborts scoring instead of silently lowering accuracy.
The endpoint must be 127.0.0.1, with no proxy or redirect following.

The dataset and any references may contain sensitive material. Use only
approved/local data. The report is not a model-performance benchmark,
semantic scorer, hallucination detector, or calibrated quality evaluation.
Real model quality has not been verified by this documentation or CI. A developer running this command can combine its locally observed quality report with a repeated concurrency sweep using the [real-model evidence-bundle guide](../benchmarks/real-model-evidence-bundle.md).

## Before/after quality guard

The [serving comparison CLI](../benchmarks/controlled-comparison.md) checks the same quality dataset SHA-256, model revision and decoding settings in both evidence bundles, and labels any exact- or normalized-match regression as ineligible for a positive optimization conclusion. These exact-match metrics are not a substitute for semantic, factuality or safety evaluation.

## Exploratory arithmetic quality-regression case

An independent [October 14 arithmetic observation](observations/2026-10-14-arithmetic-model-smoke.md)
records an operator-reported `2+2` discrepancy: `llama3.2:1b`
returned `2` in 3/3 native API trials, whereas
`llama3.2:latest` returned `4` in 3/3. It is a two-question,
separately versioned [smoke dataset](workload.arithmetic-regression.v1.jsonl),
**not** a revision of the historical 36-question workload or evidence
of a gateway correctness defect. The attached offline CI test validates
only the dataset contract and synthetic answer scoring; live model
reproduction remains an operator-run experiment.

## Larger synthetic reference workload

A new versioned **36-question** deterministic reference dataset is available at `docs/nlp-eval/workload.v1.jsonl`, with answer-only geography, arithmetic and computing acronym questions. See [workload design v1](../benchmarks/workload-design-v1.md) for its limitations. It can support initial local quality-regression checks, but its answers do not test nuanced semantic quality, factuality under ambiguity, or model safety. The older two-question example remains available and unchanged for smoke tests.
