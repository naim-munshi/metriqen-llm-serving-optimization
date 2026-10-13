# Synthetic serving workload v1: construction and limitations

This repository includes two **new, versioned, synthetic** datasets.
They are *not* real-model evidence, a benchmark run, an independently
validated general-purpose quality set, or an endorsement of a model.

## Performance prompts: docs/benchmarks/workload.v1.jsonl

Thirty-six original prompts with the same schema accepted by the local
benchmark runner (exactly one messages array per record). They are ordered
into **character-length** categories:

| Line numbers | Number | Input length (characters) | Intent |
| --- | ---: | ---: | --- |
| 1-12 | 12 | 45-160 | Short technical questions |
| 13-24 | 12 | 250-700 | Medium engineering cases |
| 25-36 | 12 | 850-2000 | Longer, synthetic technical scenarios |

**Character buckets are not token-length buckets.** Model-specific
tokenization, special tokens and system templates change the actual token
count. The Python runner does not currently tokenize prompts and cannot
report validated prefill tokens/sec. These categories are useful for a
repeatable exploratory comparison, not statistical sampling from production
traffic. Template-shaped long contexts also limit diversity.

All prompt text is original, contains no user/employee/private data, and
requests bounded textual answers. The same requests may complete with
different output lengths; compare both end-to-end latency and reported
output-token throughput rather than interpreting raw wall time alone.

## Reference quality: docs/nlp-eval/workload.v1.jsonl

Thirty-six narrow deterministic-answer questions with unique IDs and
exactly three fields: id, messages, reference. They cover:

- q01-q12: geography capitals;
- q13-q24: basic arithmetic;
- q25-q36: common computing acronym expansions.

All prompts ask for answer-only text to make reference matching possible.
The evaluator scores exact/normalized string matches and has no rubric
for semantic equivalence, factual nuance, fluency, calibration or safety.
Punctuation, alternate spellings and a model's formatting can change
exact match even when humans would accept an answer. Results do not
measure modern instruction-following competence broadly.

The default comparator requires >=30 references and >=30 measured
requests per run for its descriptive-only status, so these datasets
meet the **count** thresholds when used with an appropriate sweep.
Meeting a count threshold does **not** make them statistically
representative or establish equivalent semantic model quality.

## Preserving evidence history

The older `workload.example.jsonl` files remain unchanged.
A new dataset has a new SHA-256 fingerprint. Do not edit v1 content
silently or mix v1 with earlier example-based experiment results.
Any substantive future revision must use a new file/version and state
its sampling assumptions. A before/after experiment must use the
*same* exact dataset files and model revision across both conditions.

A versioned workload can be measured locally after Ollama and the
loopback gateway are running:

~~~sh
python3 -m tools.benchmark.sweep \
  --model llama3.2:1b --model-revision "$MODEL_DIGEST" \
  --runtime ollama-0.30.8 --hardware 'Apple M2 Metal' \
  --dataset docs/benchmarks/workload.v1.jsonl \
  --repeats 3 --requests 36 --warmup 2 --max-tokens 128 \
  --output-dir reports/local/v1-baseline-01
~~~

Use the same model digest, runtime/hardware, generation limits,
load and quality dataset for the candidate experiment. Evaluate using
`docs/nlp-eval/workload.v1.jsonl` via `tools.nlp_eval.online`,
then follow [the evidence bundle](real-model-evidence-bundle.md) and
[controlled comparator](controlled-comparison.md) runbooks.

**Resource note:** nine runs of 36 measured requests plus warmups can
take a long time on a laptop and may cause heating and throttling.
Record thermal/background-load conditions. Keep both services loopback
only. These commands have **not** been executed on real hardware in CI;
there are no measured v1 throughput/quality numbers in this repository.
