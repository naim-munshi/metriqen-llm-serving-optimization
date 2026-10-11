# NLP quality baseline — deterministic reference evaluation

This independent **offline** evaluation component complements the inference benchmark.
It does not judge semantic equivalence, hallucination, toxicity or model safety.

Input JSONL requires stable IDs:

References: \`{"id":"q1","reference":"Paris"}\`
Predictions: \`{"id":"q1","prediction":"paris"}\`

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

Run tests with \`python -m unittest discover -s tools/nlp_eval/tests -v\`.

Future steps: standardized NLP datasets with licensing review, tokenizer-aware workload buckets,
semantic-similarity metrics (versioned embedding model), model-based evaluation with calibration,
and reproducible quality-vs-latency comparison against real vLLM/GPU inference.
