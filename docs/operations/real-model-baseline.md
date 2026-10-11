# Real-model baseline acceptance protocol

This is an **execution protocol**, not evidence that Ollama or a GPU model has been run.
Metriqen's automated CI tests use deterministic fixtures; the developer must
execute the steps below on hardware with a locally installed Ollama runtime.

## Preconditions

1. Verify `ollama --version`, `ollama list`, and the exact model digest using
   `ollama show llama3.2:1b --modelfile` or the runtime's model metadata.
   Model tags alone are mutable and are **not** immutable artifact identifiers.
2. Record host OS, chip/GPU, unified RAM/VRAM, Ollama version, model tag and
   digest, quantization format, timestamp and whether other workloads were running.
3. Start the gateway in `services/inference-gateway`:

   ```sh
   METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 go run ./cmd/server
   ```

   This gateway has no user authentication. Keep it strictly on loopback.

4. From the repository root, verify an actual response:

   ```sh
   curl --fail-with-body -sS http://127.0.0.1:8081/v1/chat/completions \
     -H 'Content-Type: application/json' \
     -d '{"model":"llama3.2:1b","messages":[{"role":"user","content":"Reply exactly: ready"}],"temperature":0,"max_tokens":32,"stream":false}'
   ```

   Check the model's real response, not just HTTP 200 or process readiness.

## Controlled measurements

Run the **same** dataset, model, decoding settings and environment across all
configurations. Record the self-reported fields accurately; they are not
automatically verified.

```sh
python -m tools.benchmark.runner \
  --model llama3.2:1b \
  --model-revision 'REPLACE_WITH_VERIFIED_DIGEST' \
  --runtime 'REPLACE_WITH_ACTUAL_OLLAMA_VERSION' \
  --hardware 'REPLACE_WITH_ACTUAL_MACHINE_DETAILS' \
  --dataset docs/benchmarks/workload.example.jsonl \
  --stream --warmup 3 --requests 30 --concurrency 1 \
  --max-tokens 64 --output reports/local/ollama-baseline-c1.json
```

Repeat with `--concurrency 2` and `--concurrency 4`, changing the output
filename accordingly. Repeat each concurrency configuration at least three
times. Do not average away failures. Report median, spread and raw per-run
results; never treat mock-test timings as measurements.

## Quality gate

Use the same pinned model revision and hardware metadata for a separate
reference-answer evaluation. Its JSONL dataset has `id`, `messages`, and
`reference` fields.

```sh
python -m tools.nlp_eval.online \
  --model llama3.2:1b \
  --model-revision 'REPLACE_WITH_VERIFIED_DIGEST' \
  --runtime 'REPLACE_WITH_ACTUAL_OLLAMA_VERSION' \
  --hardware 'REPLACE_WITH_ACTUAL_MACHINE_DETAILS' \
  --dataset docs/nlp-eval/workload.example.jsonl \
  --max-tokens 64 --output reports/local/ollama-quality.json
```

Exact match and normalized exact match are narrow reference-string metrics.
They are not factuality, semantic similarity or hallucination measures.
Do not compare different optimization settings unless quality and workload
conditions remain comparable.

## Evidence and release gates

- Store local raw reports privately first; `reports/local/` is gitignored.
- Review prompts, response text, logs and any telemetry before sharing.
- Record hardware, model digest, runtime version, repetitions, failures,
  sampling limitations and exact commands with any published numbers.
- A valid CPU/Apple Silicon baseline does not establish NVIDIA GPU/vLLM
  performance or a speedup from batching, quantization or caching.
- CI success proves code contract behavior, **not** a successful real model run.
- If the runtime is unavailable, mark the acceptance gate **blocked** rather
  than inventing benchmark measurements.
