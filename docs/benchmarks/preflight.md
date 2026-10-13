# Read-only macOS preflight for local serving experiments

The **tools.benchmark.preflight** CLI provides a read-only pre-benchmark check
for the developer-operated Ollama/Metriqen gateway stack. It can record a
private JSON snapshot before each new experiment phase. It does **not** launch
Ollama, load a model, issue inference requests, change parallelism, start a
benchmark, authenticate hardware, or inspect actual Ollama runtime slots.

## Requirements

- macOS developer host on AC power; do not start a new benchmark on battery.
- Exactly one existing Ollama serve process whose visible process environment
  matches the expected OLLAMA_NUM_PARALLEL value (1 or 2).
- Local Ollama API on 127.0.0.1:11434 and Metriqen gateway on
  127.0.0.1:8081, both responding with their expected API contracts.
- The installed llama3.2:1b model digest must match the **pinned digest**
  chosen independently for the study. Never silently accept a newly pulled
  revision.

From the repository root (after starting the two local services):

~~~sh
python3 -m tools.benchmark.preflight \
  --expected-model-digest baf6a787fdffd633537aa2eb51cfd54cb93ff08e28040095462bb63daf552878 \
  --expected-parallelism 1 \
  --output "reports/local/preflight-A1-$(date +%Y%m%d-%H%M%S).json"
~~~

Use expected parallelism 2 for the B phase and unique private report
paths for every phase. The digest above comes from the operator-reported
2026-10-13 local experiment; confirm it remains your intended pinned model.
The repository excludes reports/local/ from Git tracking.

The CLI refuses to claim preflight success when AC power, the installed model,
gateway, Ollama version, or expected process parallelism cannot be confirmed.
It blocks multiple Ollama serve processes. Exit code 2 denotes a blocked
preflight or invalid output; blocked results can still be saved. Reports
are created exclusively and will never overwrite existing evidence.

A successful check returns only the status
**preflight_checks_passed_slot_verification_required**. This is deliberately
**not an attestation of actual active slots**. Operator inspection of model-load
logs for n_seq_max/n_slots, phase timing, thermal/power background conditions,
and quantization is still mandatory. The process environment proves only the
configured variable was visible.

Only fixed loopback URLs are called; proxies and redirects are disabled.
Commands use argument vectors (no shell), and the JSON report never contains
full process environments, authentication headers, prompts or generated
responses. Memory-free percentage is descriptive, not a thermal or memory
pressure guarantee.

CI exercises this command with synthetic fixtures on Linux. CI does not
verify a real Mac, local runtime slot count or real benchmark speedups.
