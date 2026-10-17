# Local inference monitoring in Model Manager

**Scope:** a local diagnostic panel using real fixed Go gateway counters, not an inference
optimization result or production observability stack.

After enabling the local model registry control API (see
[Model Manager setup](local-model-registry.md)), open
http://127.0.0.1:8000/console/models and connect with the local operator
token. The **03 / Inference Operations** section fetches live metrics from
the direct localhost Go gateway on port 8081. Refresh happens when
connecting, when clicking Refresh metrics, after successful inference,
and approximately every 15 seconds while the page is visible and idle.

The operator-authenticated endpoint `GET /api/v1/local/monitoring` reads
only fixed direct-loopback read-only Go endpoints:
- `/metrics/inference` (v3): admitted attempts, active, terminal outcomes
  and post-admission duration histogram/totals;
- `/metrics/admission` (v2): active/waiting capacity, queue rejection and
  queue wait timeout counters;
- `/metrics/upstream` (v1): sanitized transport/protocol failure events.

The backend uses strict schema/counter validation, two-second timeouts,
a bounded response and an HTTP client with redirects and proxying
disabled. The secret is checked before any Go metrics are fetched,
and is never forwarded. A disconnected gateway renders **unavailable**,
not invented zero counts; other local Model Manager functions remain usable.

## Definitions and restrictions

| Display | Meaning |
|---|---|
| Admitted attempts | Total inference requests accepted into the gateway's active slots |
| Nonstream complete | Completed nonstream gateway requests |
| Active inference | In-progress admitted inference |
| Gateway queue | Currently waiting requests |
| Rejected: queue full | Cumulative full-queue rejection, outside admitted attempts |
| Queue wait timeouts | Cumulative timeout before admission |
| Pre-response failures | Failed admitted requests before a response began |
| Mean inference duration | Mean of observed post-admission durations, excluding FIFO queue wait |
| Upstream failure events | Failure-event totals, **not** count of failed client requests |

**The numbers reset on every gateway restart.** They are aggregates, not
persistent time series, not per-model/per-tenant tracking, and not request logs.
No prompt or response content is collected. Do not present these as
requests/second, p95, TTFT, GPU usage, performance improvement, uptime,
or service-level availability. Use the separate controlled benchmark
workflows for reproducible research evidence.

## Manual acceptance

On your Mac, ensure the Go gateway is running on 127.0.0.1:8081.
Load the approved model in Model Manager, run a short inference request,
then refresh metrics. The admitted/nonstream counters should reflect
the gateway request. Restart the Go gateway separately and observe
the counters reset. Stop the gateway and observe a monitoring-unavailable
message without losing registered models. CI covers synthetic HTTP
contracts only; real Mac/browser behavior remains operator validation.
