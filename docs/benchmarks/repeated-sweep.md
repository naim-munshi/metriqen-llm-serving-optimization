# Repeated real-model concurrency sweep

This tool orchestrates measured requests through the actual local Go gateway.
It does **not** deploy a model, perform GPU optimization or fabricate results.
CI exercises the orchestration only against synthetic fixture reports.

## Prerequisites

- Latest main checkout and Python 3.12+.
- Ollama running on 127.0.0.1:11434 with an installed model.
- Metriqen Go gateway running on 127.0.0.1:8081.
- Keep both local servers running during the entire sweep.
- Record accurate Ollama version, actual hardware specification and a
  verified 64-character model digest (a model tag is not immutable).

From the repository root, obtain the exact local Ollama digest:

~~~sh
MODEL_DIGEST=$(curl -fsS http://127.0.0.1:11434/api/tags | python3 -c 'import json,sys; print(next((m["digest"] for m in json.load(sys.stdin)["models"] if m["name"]=="llama3.2:1b"), ""))')
test -n "$MODEL_DIGEST" && printf 'Model digest: %s\n' "$MODEL_DIGEST"
~~~

Run an experiment in a **new output directory**:

~~~sh
python3 -m tools.benchmark.sweep \
  --model llama3.2:1b \
  --model-revision "$MODEL_DIGEST" \
  --runtime ollama-0.30.8 \
  --hardware 'Apple M2 Metal; record actual RAM and macOS version' \
  --dataset docs/benchmarks/workload.example.jsonl \
  --repeats 3 --requests 12 --warmup 2 \
  --max-tokens 128 --timeout 120 \
  --output-dir reports/local/ollama-m2-sweep-01
~~~

Change the runtime/hardware labels to reflect actual conditions. The command
runs synchronously and may take several minutes. The gateway is local-only
and unauthenticated; never expose it publicly.

## Method

- Three repetitions each at concurrency **1, 2 and 4**, nine runs total.
- Concurrency order rotates by repetition: (1,2,4), (2,4,1), (4,1,2).
- Each run excludes two sequential warmup requests and measures 12 requests.
- Dataset bytes, model digest, runtime, hardware, output-token cap and decoding
  temperature remain fixed. Stream usage is requested by default.
- For OpenAI-compatible runtimes without streaming usage support, choose
  --no-stream-usage. Token throughput then remains null, never estimated.
- Every new run report records `timing.started_at_utc`,
  `timing.finished_at_utc` (RFC3339 UTC milliseconds), and a monotonic
  `timing.elapsed_seconds` duration **including sequential warmups**.
  `results.wall_seconds` still measures only the post-warmup requests.
  This exposes run ordering and time-of-day drift, but does **not** measure
  GPU temperature, effective prompt cache hits or energy consumption.
- Every run produces its own private report. Only after all nine succeed does
  the tool write sweep-summary.json with median/min/max **of per-run metrics**, including p50 and p95 latency/TTFT.
  These are not pooled request percentiles or confidence intervals.
  The sweep summary also copies the validated timing into each `run_files`
  entry. Evidence bundling and offline comparison reject mismatched timing,
  implausible durations, or overlapping timestamps.
- Existing legacy reports without timing remain valid for historical
  analysis. Never invent or backfill clock timestamps for old runs.
- Any failed or inconsistent run aborts the sweep, retains the partial reports
  for investigation, and does NOT write an aggregate summary.
- Pre-existing output directories are rejected to protect earlier evidence.

Reports under reports/local/ are gitignored and should be inspected for
privacy before being shared. Neither generated text nor prompts are put into
the aggregate report.

## Read results

~~~sh
python3 -c 'import json; p="reports/local/ollama-m2-sweep-01/sweep-summary.json"; r=json.load(open(p)); print(json.dumps(r["summary"], indent=2))'
~~~

Check success rate, per-run p50/p95 latency and TTFT, token-usage coverage,
response length, and throughput variation. With only twelve requests per run,
p95 is noisy and not suitable for production SLO claims.

For a reproducible, **offline per-run variability review**, use the [run-variability reviewer](run-variability-review.md). It checks the raw files against the aggregate summary, records run order, and flags unusually wide repeat-run ranges without dropping observations or claiming causality.

## Limits and follow-up

This sweep measures throughput and latency but **not model answer quality**.
After the sweep finishes, follow the [evidence-bundle workflow](real-model-evidence-bundle.md) to run the separate NLP reference evaluation and validate the raw reports together. Use the same pinned model and honest runtime/hardware labels. To compare two matching and separately verified runs, follow the [controlled-comparison protocol](controlled-comparison.md). Do not claim optimization speedup without a
controlled before/after configuration change at comparable quality.

The report is not a cryptographic proof of GPU execution or hardware
identity. CI tests use synthetic reports, not Ollama measurements.

## Versioned synthetic evaluation workloads

The basic copy/paste example above uses a tiny three-prompt dataset; treat its results as a smoke test. For a more varied but still **synthetic, unrepresentative** baseline, use `--dataset docs/benchmarks/workload.v1.jsonl` and a fresh output directory, with e.g. 36 requests per run. Pair it with `docs/nlp-eval/workload.v1.jsonl` via the quality evaluation CLI. See [workload design v1](workload-design-v1.md), including tokenizer and thermal limitations. Never silently switch datasets between baseline and candidate.
