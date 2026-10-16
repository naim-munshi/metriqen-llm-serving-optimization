# Local HAProxy: two Go inference gateway replicas

**Development experiment, not a production reverse proxy or multi-tenant
authorization boundary.** No external network binding is permitted.

This optional topology uses industry-standard HAProxy, configured in
[infrastructure/load-balancer/haproxy.local.cfg](../../infrastructure/load-balancer/haproxy.local.cfg).
All components bind **127.0.0.1 only**:

    Browser -> local FastAPI :8000 (operator approved-model auth)
            -> HAProxy :8090 (leastconn, POST chat only)
               -> Go gateway :8081 -> local Ollama :11434
               -> Go gateway :8082 -> same local Ollama :11434

The proxy accepts **POST /v1/chat/completions only** and rejects other
methods/paths with 404. It strips untrusted Forwarded/X-Forwarded-* headers.
It uses active /health/upstream checks (HTTP 200) for each gateway, and a
least-connections policy to reduce head-of-line blocking from long-lived
streams. Each Go gateway has separate in-process admission and outcome
counters; the Operations console on :8081 only sees that process.

## Procedure (operator-run only)

1. Confirm the existing Ollama **single** process is available on
   127.0.0.1:11434. If Ollama.app already owns the port, do **not**
   launch another \`ollama serve\`.
2. Terminal 2: keep gateway 1 on 127.0.0.1:8081, pointing to the local
   Ollama process; do not restart it if already correct.
3. Terminal 5: run gateway 2 on a *different loopback port*:

   ~~~sh
   cd ~/metriqen-llm-serving-optimization/services/inference-gateway
   METRIQEN_GATEWAY_BIND_ADDR=127.0.0.1:8082 \
   METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 \
   METRIQEN_ALLOWED_MODELS=llama3.2:1b \
   METRIQEN_QUEUE_DEPTH=0 \
   go run ./cmd/server
   ~~~

   The same operator-only model allowlist should be used on gateway 1.
   This local development example does **not** configure the separate
   optional gateway bearer token; for production use, authenticated
   and scoped Gateway authorization is still a blocking requirement.
4. Terminal 6: install HAProxy separately if necessary, and confirm
   the checked-in config is syntactically valid:

   ~~~sh
   cd ~/metriqen-llm-serving-optimization
   haproxy -c -f infrastructure/load-balancer/haproxy.local.cfg
   haproxy -db -f infrastructure/load-balancer/haproxy.local.cfg
   ~~~

   Run foreground; Ctrl+C stops only HAProxy. Do not expose port 8090
   via public tunnels, Docker port bindings, NAT or a reverse proxy.
5. To send the Model Manager's **approved** single-turn chat through the
   new proxy, set the following on Terminal 4 **before** restarting the
   existing FastAPI server with its existing local-model operator token
   and private database variables:

   ~~~sh
   export METRIQEN_LOCAL_CHAT_GATEWAY_PORT=8090
   ~~~

   The server permits only \`8081\` (original direct route, default) or
   \`8090\` (fixed HAProxy loopback listener). No request can choose the
   proxy port, scheme, host or target backend. Leave it unset to retain
   the original direct route.

## Acceptance, limitations and failure handling

- The GitHub CI job installs HAProxy and runs its real config checker;
  control-api tests reject arbitrary ports/remote URLs. This is *syntax
  and safety admission*, not a traffic/throughput test.
- Check each Go backend independently with localhost /health/upstream
  and /metrics/inference. The proxy will not claim readiness when both
  backends return non-200 status. Probe only establishes Ollama API
  reachability, **not loaded-model readiness**.
- Streaming SSE is not intentionally buffered by the HAProxy configuration;
  verify content flush, cancellation, client timeouts and [DONE] termination
  with real Ollama before claiming streaming compatibility through the proxy.
- Two Go gateways sharing one **8 GiB Mac Ollama** do *not* double model
  capacity or constitute GPU load balancing, continuous batching, distributed
  quotas or a verified latency/throughput improvement. In fact, higher
  concurrency could reduce reliability or increase latency.
- The two gateway processes can each admit four requests; aggregate
  capacity is not coordinated in Redis. A global shared quota, protected
  ingress, verified model-worker affinity, deployments and durable rollback
  remain necessary for production.
- Operator bearer token on FastAPI does not authorize direct access to
  /v1/chat/completions on the individual gateways. Keep every service bound
  to loopback. Do not use this sample as an Internet-facing API service.

See the [full identity/security/Redis/LB design](../architecture/identity-data-redis-load-balancing.md).
