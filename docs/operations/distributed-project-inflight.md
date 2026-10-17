# Distributed per-project in-flight inference leases (localhost opt-in)

This is a **separately enabled request-concurrency limit** shared across
independent Go gateway and Python control API replicas via one Redis 7
instance. It **does not replace** the existing per-project requests/minute
budget, 4-active local Go admission scheduler or deployment readiness check.

## Setup: enable both sides, never partially

For each trusted **loopback** control API replica, configure these together:

- `METRIQEN_PROJECT_INFLIGHT_REDIS_URL`: an authenticated Redis ACL URL
  `redis://inflight_operator:REPLACE_WITH_LONG_RANDOM_PASSWORD@127.0.0.1:6379/0`
  pointing to **one shared** private Redis instance.
- `METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET`: 32–512 printable bytes, an
  independent random high-entropy secret, **identical across replicas**.
- `METRIQEN_PROJECT_INFLIGHT_MAX_ACTIVE`: 1–16, **default 2** if the
  URL+HMAC are provided, consistent across all API replicas.

For each trusted **loopback** Go inference gateway replica:

- `METRIQEN_GATEWAY_PROJECT_INFLIGHT_LEASES=1`
- Existing `METRIQEN_GATEWAY_PROJECT_AUTH_URL` (pinned literal
  `http://127.0.0.1:8000/internal/gateway/authorize`) and
  `METRIQEN_GATEWAY_PROJECT_AUTH_TOKEN` (shared internal API service
  secret) remain mandatory.
- Control API needs the corresponding `METRIQEN_GATEWAY_INTERNAL_TOKEN`,
  pinned OIDC identity verification and authoritative PostgreSQL
  organization/project membership. Continue to use the existing
  `METRIQEN_ENFORCE_DEPLOYMENT_ROUTING=1` on both services when you
  also require project deployment-level model isolation.

Use an ACL user restricted to **only**
`metriqen:project-inflight:v1:*`, with permissions to `EVAL`,
`TIME`, `ZREMRANGEBYSCORE`, `ZCARD`, `ZADD`, `PEXPIRE`, `ZREM`
(and `PING` if needed). Do **not** use an unauthenticated default Redis
user. The existing per-project request-rate budget uses a different HMAC
secret, key namespace and ACL policy. Startup rejects partial settings.
If Go requests permits but Python has not enabled them, Go rejects missing
permit evidence with 503; if Python enables permits but Go has not, Go
refuses the unexpected positive authorization response. Do **not** mix
strict, guarded-only and unguarded gateways behind the same HAProxy.

## Execution and failure semantics

1. Go validates a dedicated Bearer JWT and canonical project ID and calls
   the pinned control API with only the JWT, project ID, optional deployment,
   and internal service credential. It does **not** forward prompts yet.
2. Control API verifies current JWT, SQL tenant membership,
   `inference.invoke`, optional SQL deployment revision and optional
   request-rate quota. Only then it atomically acquires a project permit in
   Redis. The project Redis key contains a *keyed HMAC* of authoritative
   organization+project IDs; no raw tenant IDs, JWT, model tags, prompts,
   outputs or user identities are kept in Redis.
3. Redis Lua checks **Redis server time**, discards expired permits, checks
   the project-wide ZSET cardinality, then inserts one cryptographically
   random 128-bit permit ID with a **240-second expiry**. A full project
   returns HTTP **429** and a fixed `Retry-After: 1` without admitting the
   prompt or calling Ollama. Redis outage/unexpected reply returns generic
   **503** and denies inference.
4. Go accepts exactly one positive decision with a matching project,
   strict model revision if configured, and a syntactically valid
   `lease.scope` and `lease.permit`. It performs existing prompt
   validation, local model allowlist, pinned Ollama digest verification
   (when strict) and local FIFO admission. On every handler return,
   including malformed prompts, upstream 5xx and client cancellation,
   Go attempts an internal authenticated POST to
   `/internal/gateway/release` **without a JWT**, using a fresh bounded
   context even if the client has disconnected. The release is idempotent.
5. Go's local inference HTTP server bounds body reads (10s) and response
   writes (130s); the upstream request itself has a 120s deadline; the
   middleware adds a 150s end-to-end execution context. The larger 240s
   lease expiry is crash recovery, **not a request timeout**. A failed
   release or crashed gateway holds the permit until Redis expiry and
   subsequent atomic reaping. Redis availability is required for *new*
   authorizations, but a transient release outage cannot double-admit.

**Residual limitations:** An intentionally stalled or compromised gateway
handler that survives beyond the 240s permit expiry could allow
oversubscription; this TTL-based crash-recovery policy is **not a hard
distributed kill switch or GPU scheduler**. Redis failover, clocks,
reconfiguration, ACL rotation, and replica consistency must be tested in
the target runtime. Revoking a user or stopping a deployment affects the
next authorization, not inference already in progress. A malicious local
process with access to the internal service credential can affect permits.
No automatic token counting, generated-token quotas, global cost budgets,
live GPU isolation, mTLS or production identity integration is included.
The old unguarded localhost route still bypasses these controls if exposed.

## Verification and operator acceptance

CI tests simulate Go replicas with a shared authorization service, confirm
the third concurrent call is denied while two active requests run,
verify automatic release on normal completion, HTTP errors and cancellation,
and reject malformed/partial permits. Real ephemeral **Redis 7** CI uses
two clients, a scoped ACL, concurrent acquire attempts, replay-safe release,
project/organization key separation and expired-score crash reclamation.

For a real local integration test, separately configure trusted PostgreSQL,
OIDC, Ollama, control API and two gateway instances and then check:
- two allowed active requests for the same project (default limit 2);
- third same-project request gets 429 without reaching Ollama;
- a different project's request remains independently admissible;
- stopped/unready/revoked project returns 404/409 before any permit;
- released permits become available immediately;
- after deliberate gateway termination, abandoned permits recover within
  240 seconds (plus next attempt);
- with Redis unavailable, new requests fail closed (503).

These are **operator validation steps, not a claim they were run on a Mac or
a production model**. No performance improvement or GPU throughput number
is claimed by this security/capacity milestone.
