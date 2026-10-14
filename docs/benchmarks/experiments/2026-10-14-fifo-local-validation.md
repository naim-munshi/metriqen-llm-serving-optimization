# Local FIFO scheduler validation — October 14, 2026

**Evidence type:** developer-supplied macOS terminal output and local
Ollama logs, reviewed after execution. Not independently reproducible CI
hardware evidence, a production stability assessment, a latency benchmark,
or a claimed optimization result. No raw model prompts or responses from
private workloads are published in this document.

## Configuration observed

The developer reported `GET http://127.0.0.1:8081/metrics/admission`
after restarting the local-only Go gateway with an opt-in queue:

| Field | Observed |
| --- | ---: |
| `schema_version` | 2 |
| `max_active_requests` | 4 |
| `max_waiting_requests` | 2 |
| `active_requests`, `waiting_requests` at snapshot | 0, 0 |
| All admission counters immediately after restart | 0 |

This checks configuration and idle state, **not** FIFO ordering under
load. The gateway is unauthenticated; do not bind it to a public address.

## Seven-request exploratory burst: admission vs. upstream failures

The developer used a Python ThreadPoolExecutor with seven concurrent
non-streaming `llama3.2:1b` requests, `temperature=0`,
`max_tokens=128`, and a longer generated-output prompt. Captured
client statuses: **one HTTP 429, six HTTP 502**. Before/after
admission metric deltas were:

| Metric | Observed delta |
| --- | ---: |
| `admitted_immediate_total` | 4 |
| `admitted_queued_total` | 2 |
| `rejected_full_total` | 1 |
| `queue_timeout_total` | 0 |
| `queue_canceled_total` | 0 |
| `queue_wait_observations_total` | 2 |
| `queue_wait_duration_total_us` | 4,081 |

The nine disjoint histogram bins recorded
`[2, 0, 0, 0, 0, 0, 0, 0, 0]`. Both observed
queue waits were within the first, up-to-10-ms bucket. The total wait
time (4.081 ms) is the sum for two queued outcomes, not response time,
throughput, time to first token, or a representative queue latency.

The sampling loop reported `peak_active=0` and `peak_waiting=0`.
It polled every 50 ms and evidently missed the brief admission
transitions; these sampled zeros **do not invalidate or reproduce**
the counter deltas. They cannot establish true peak concurrency.

Immediately afterward, a *single* gateway completion returned
`502` with sanitized code `upstream_unavailable`, and a direct
Ollama API connection to `127.0.0.1:11434` was refused. These
observations demonstrate upstream unavailability at the **later**
diagnostic check and are consistent with the burst's 502s; they do
not determine exactly when the Ollama process stopped or why.
No crash/OS termination log was supplied for the previous process.
The burst is **not** a successful seven-request throughput benchmark,
a proof of first-in-first-out ordering, or evidence that the Go gateway
caused an Ollama shutdown.

## Recovery and real-model smoke

The developer restarted `ollama serve`. Restart logs reported:

- Ollama 0.30.8 on Apple M2 / Metal with 8 GiB total system memory;
- `OLLAMA_NUM_PARALLEL:1`; `n_seq_max=1`, `n_slots=1`;
- free system memory of approximately 1.4 GiB **at model-load time**
  and zero free swap then (not proof of out-of-memory termination);
- installed `llama3.2:1b` full digest
  `baf6a787fdffd633537aa2eb51cfd54cb93ff08e28040095462bb63daf552878`,
  Q8_0, as shown by the developer's local Ollama model listing.

Both `/api/tags` and a single gateway `/v1/chat/completions`
smoke succeeded after restart. A separate two-request concurrent
gateway smoke test then returned **two HTTP 200 completions**,
and `GET /api/tags` returned HTTP 200 immediately afterward.
These are limited recovery checks, not sustained stability evidence.
Gateway admission capacity 4 does not mean Ollama executes four
inferences in parallel when the Ollama runner has one slot.

## Deterministic scheduler verification on the developer Mac

From `services/inference-gateway`, the developer ran:

~~~sh
go test -race -count=10 ./internal/httpapi \
  -run '^(TestFIFOAdmissionOrderAndBoundedQueue|TestOptInQueueWaitsWithoutForwardingUntilCapacityIsReleased|TestCanceledHTTPQueuedRequestNeverForwardsPrompt|TestQueueDeadlineDoesNotHoardActiveCapacity)$' \
  -v
~~~

**Reported result:** all four tests passed on each of ten
repetitions (40 passing test invocations), `PASS`, package `ok`
in **2.104 seconds**; no data race was reported by Go's race
detector. This is repeated execution of four existing deterministic
mock-upstream tests, not 40 independent model workloads and not
exhaustive concurrency verification.

The tests separately cover:

1. FIFO grant order and bounded queue saturation;
2. queued HTTP work remaining unforwarded until an active slot releases,
   and rejection with HTTP 429 / `Retry-After`;
3. canceled queued HTTP requests not reaching the upstream;
4. timeout cleanup, ensuring available capacity is not leaked.

The real-Ollama burst checks aggregate admission counters, whereas
ordering/cancellation/deadline behavior is proven only within the
scope of the deterministic tests.

## Engineering follow-up and evidence gate

1. **Diagnose the earlier process outage** before further aggressive
   live-model bursts: retain *timestamped* Ollama lifecycle/exit
   information, inspect macOS memory pressure and process-exit
   evidence, and avoid inferring OOM from free RAM alone.
2. **Separate admission health from upstream health:** report HTTP
   upstream transport/status failures independently from admission
   decisions, with strict aggregation and no user prompt/error-body
   leakage. Define semantics before adding new counters.
3. **For future serving-policy comparisons**, pin model full digest,
   runtime, effective runner slots, hardware, power, request workload,
   queue configuration and decoding. Collect repeatable success
   rate, quality, TTFT and latency evidence; verify the upstream is
   healthy before and after each run. The October 14 burst cannot be
   reused as a valid performance baseline.

**No gateway behavior, scheduler implementation, historical published
experiment or quality-scoring thresholds were modified for this
observation.**
