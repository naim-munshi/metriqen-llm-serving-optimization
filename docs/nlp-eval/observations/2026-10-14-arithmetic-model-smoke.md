# Arithmetic model-quality smoke case (operator-reported, October 14, 2026)

This is an **exploratory model-quality observation**, not a serving
benchmark, validated accuracy estimate, or claim of a newly introduced
gateway regression. The operator ran deterministic-temperature local
Ollama checks on a developer Mac, and supplied terminal outputs. The
raw machine logs, immutable model digests, run order and complete hardware
state were not independently attested or published.

## Observed outputs, not CI claims

For the exact prompt **"What is 2 plus 2? Answer with one number."**,
with Ollama Native `/api/chat`, temperature 0 and
`num_predict=32`:

| Model tag | Reference | Operator-reported predictions | Exact matches |
| --- | --- | --- | ---: |
| `llama3.2:1b` | `4` | `2`, `2`, `2` | **0/3** |
| `llama3.2:latest` | `4` | `4`, `4`, `4` | **3/3** |

Separate single-request checks reported that:

- `llama3.2:1b` produced `12` for **"What is 7 plus 5? Reply with only
  the number."**, using the native API (one observed run).
- Both the Go gateway and the direct Ollama OpenAI-compatible
  `/v1/chat/completions` endpoint returned `2` for the same `2+2`
  prompt in a prior spot check; Ollama native also returned `2`.

This observation **does not isolate** the model weights from the model
template, quantization, installation, cache state or other runtime factors.
It does not demonstrate a general accuracy gap, an API defect, or gateway
prompt corruption. Repetition at temperature 0 is not independent
statistical replication. The two different question outcomes must not be
combined into a claim of `1b` quality without a pre-registered dataset.

## Reproduce through the existing evaluator

The exact observed prompts and their unambiguous reference answers are
in the separate
[arithmetic regression dataset](../workload.arithmetic-regression.v1.jsonl).
**Do not edit** the previously published 36-question
[`workload.v1.jsonl`](../workload.v1.jsonl) or reuse historical quality
scores as a matched baseline for this case.

Run the independent, sequential OpenAI-compatible quality evaluator
from repository root, with **Ollama running on localhost**:

~~~sh
# Capture environment and immutable model tags/digests before any comparisons.
ollama --version
curl -fsS http://127.0.0.1:11434/api/tags | python3 -m json.tool

# These are model-quality observations, not benchmark throughput runs.
# The existing evaluator uses localhost only, temperature 0, non-streaming.
for model in llama3.2:1b llama3.2:latest; do
  label=$(printf '%s' "$model" | tr ':' '-')
  for run in 1 2 3; do
    python3 -m tools.nlp_eval.online \
      --endpoint http://127.0.0.1:11434/v1/chat/completions \
      --model "$model" \
      --model-revision "REPLACE_WITH_FULL_MODEL_DIGEST_FROM_API_TAGS" \
      --runtime "REPLACE_WITH_OLLAMA_VERSION" \
      --hardware "RECORD_ACTUAL_MACHINE_AND_POWER_STATE" \
      --dataset docs/nlp-eval/workload.arithmetic-regression.v1.jsonl \
      --max-tokens 32 \
      --output "reports/local/arithmetic-${label}-run${run}.json" || exit 1
  done
done
~~~

**Replace every `REPLACE_...` and hardware placeholder with observed
metadata first.** The online evaluator labels revision/runtime/hardware
as operator supplied and does not independently attest these values.
Each report retains only dataset hash, aggregate quality, requested decoding,
latency and environment labels—not prompts or model responses. It makes two
requests per run. An invocation's `exact_match.correct` counts matches
out of two, and is **not** the single-question 0/3 vs 3/3 statistic above.

Three runs by two models yield six small reports and twelve generation
requests. File paths are under git-ignored `reports/local/`; do not
commit raw responses or private machine details. Run order is not
counterbalanced. Do not compare latency or throughput causally from this
smoke procedure, and do not treat string match as a semantic or safety
assessment.

## CI boundary

The offline Python test `test_arithmetic_regression_dataset.py` verifies
dataset integrity and synthetic reference scoring only. **It never invokes
Ollama, claims a production model passed, or hard-codes expected live model
failures as deterministic CI requirements.** A different model, model
revision or decoding condition represents a separate experimental
configuration.

Follow-up: build a broader, pre-registered and independently reviewed
quality set, pin full digests/quantization and runtime, inspect model
configuration, and compare across repeated controlled runs before
making quality-versus-serving-performance claims.
