# Real-model benchmark evidence bundle

The bundle connects a **completed repeated concurrency sweep** with a
separate **real local NLP reference-quality evaluation**. It reopens all
raw benchmark reports, checks the aggregate against them, checks the
model/runtime/hardware and decoding profile of the quality report,
and calls the existing strict evidence report gate.

The bundle does **not** prove model execution, GPU identity or optimized
performance. GitHub CI uses synthetic fixtures. The operator must attest
real execution after observing an actual local model response.

## 1. Finish the real concurrency sweep

Follow the [sweep runbook](repeated-sweep.md). Wait for
\`sweep-summary.json\`; any aborted run must be investigated, not excluded.

## 2. Run model answer-quality checks separately

Keep the same model, exact digest, runtime and hardware labels as the sweep.
From the repo root, with the local Go gateway and Ollama running:

~~~sh
python3 -m tools.nlp_eval.online \
  --model llama3.2:1b \
  --model-revision "$MODEL_DIGEST" \
  --runtime ollama-0.30.8 \
  --hardware 'Apple M2 Metal' \
  --dataset docs/nlp-eval/workload.example.jsonl \
  --max-tokens 128 \
  --output reports/local/ollama-m2-sweep-01/quality.json
~~~

These narrow exact-match/normalized-match scores are **not** semantic quality,
factuality or hallucination metrics. The example dataset contains only
two questions; it is not a rigorous production-quality evaluation.

## 3. Verify environment, then attest the evidence

Inspect the actual quantization details using \`ollama show llama3.2:1b\`,
the local OS using \`sw_vers\`, and the model digest in the completed sweep.
Specify accurate labels; do not invent hardware configuration.

~~~sh
MODEL_QUANTIZATION='ENTER_ACTUAL_QUANTIZATION_FROM_OLLAMA_SHOW'
python3 -m tools.benchmark.evidence_bundle \
  --sweep-dir reports/local/ollama-m2-sweep-01 \
  --quality-report quality.json \
  --host-os "$(sw_vers -productName) $(sw_vers -productVersion)" \
  --quantization "$MODEL_QUANTIZATION" \
  --timestamp-utc "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --notes 'Human-reviewed local run; record RAM, power and background workload' \
  --confirm-real-model-executed
~~~

Replace the quantization and note details with **verified facts**. The
timestamp records operator attestation at bundle creation, not the
individual experiment start time.

The \`--confirm-real-model-executed\` flag is an **explicit human declaration**,
not a GPU attestation mechanism. Never use it for synthetic reports.

The CLI refuses to overwrite an existing evidence-manifest.json. It also
rejects partial sweeps, duplicate run identities, report path traversal,
inconsistent aggregate metrics, failed requests, metadata mismatches, and
malformed or mismatched quality reports.

## 4. Independent report consistency check

~~~sh
python3 -m tools.benchmark.evidence_gate \
  reports/local/ollama-m2-sweep-01/evidence-manifest.json --verify-reports
~~~

This validates data consistency, not authenticity. Human review remains
necessary. A baseline does not prove an optimization speedup.

## Evidence hygiene

Never commit reports under reports/local/ without explicitly reviewing
their metadata. Generated reports intentionally exclude prompts and model
responses. Keep raw runs, quality report and manifest together locally for
later reproduction and review.
