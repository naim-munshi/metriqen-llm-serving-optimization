# On-demand Ollama API health probe

Use the **loopback-only** Go gateway's `GET /health/upstream` to check
whether its configured local Ollama HTTP API responds to a version probe.
This endpoint is separate from `GET /health/ready`, which only indicates
that the gateway process is responsive.

~~~sh
curl -i --max-time 5 http://127.0.0.1:8081/health/upstream
~~~

| Condition | HTTP status | JSON status |
| --- | --- | --- |
| Ollama `GET /api/version` returns HTTP 200 and a short valid JSON object with a nonempty `version` string | 200 | `reachable` |
| Ollama connection refused, timed out, redirected, returned non-200, invalid JSON or excessively large body | 503 | `unreachable` |
| No inference upstream configured | 503 | `disabled` |

Responses are sanitized, e.g. `{"status":"reachable","probe":"ollama_api_version"}`.
They contain **no model IDs, prompt text, upstream URL, error bodies or
version details**. A failed probe does not identify the process crash
cause or imply that the model itself is unavailable on every request.

The endpoint is deliberately limited:

- Uses the **configured** local upstream origin only; no caller-supplied
  URL or authentication headers are forwarded. External upstreams
  remain blocked by startup validation.
- Probes the lightweight Ollama `/api/version` endpoint; **no model
  loading, chat generation, or inference concurrency slot reservation**.
- No redirects or environment proxy following. Response body capped at
  **4 KiB**, and the probe has a **2-second context deadline**.
- Read-only and invoked **only when requested**. It does not run in the
  background, poll repeatedly, auto-restart Ollama, or retry chat
  requests; no new scheduler behavior is introduced.
- A probe's failures **do not increment inference failure counters**
  in `/metrics/upstream` (which track admitted inference attempts).
- It tests HTTP API reachability only. It **does not** prove that a
  specified model is installed/loaded, that GPU inference works,
  that new tokens can be generated, or that production readiness
  requirements are satisfied.

## Safe manual recovery procedure

If the gateway process responds but this endpoint returns 503:

1. Check the Ollama process and reachability **directly**, without
   generating model text: `curl --max-time 5 -i http://127.0.0.1:11434/api/version`.
2. Inspect operator-controlled Ollama logs and current resource pressure
   before changing configuration; avoid attributing an unexplained
   shutdown to OOM without crash/exit evidence.
3. If Ollama is stopped, restart it **manually** with `ollama serve`
   in a separate Terminal, or use the operator's existing service
   manager. Do not launch a second Ollama process if one is already
   bound to port 11434.
4. Repeat the direct version check, then
   `GET /health/upstream`, then a single bounded test generation.
   Keep historical experiments unchanged and treat restart as a new
   runtime phase.

**Never** publish the unauthenticated gateway or health endpoints.
Neither static process readiness nor this API reachability check is
a substitute for authenticated inference, quotas, model readiness
checks, persistent observability or high availability.

See [upstream failure metrics](upstream-failure-metrics.md) and
[local inference operations](local-inference.md) for complementary
aggregate diagnostics and existing security boundaries.
