# Architecture: Identity, Data, Redis, and Load-Balanced Serving

Status: **target architecture**, not a production deployment claim.
Date: 2026-10-16.

## Current working scope vs next gates

The present platform runs locally: a FastAPI control API, Go inference
gateway, real Ollama model, local model operator console, and independently
tested PostgreSQL project membership seam. The new profile endpoints in
this milestone provide verified-user profile persistence **only when** both
a trusted OIDC access-token verifier and the local PostgreSQL database have
been explicitly configured and migrated. There is still **no** browser
signup/sign-in, live identity provider, OIDC session or production token issuer.

The local operator token is a separate development secret. It is neither an
OIDC user token nor evidence of authorization to access tenant resources.
Go inference still has no enforcement of control-plane tenant membership.

## Intended data flow

    User browser (HTTPS)
           |
    TLS ingress / WAF / rate limits
           |
    OIDC Authorization Code + PKCE, state + nonce
           |  (trusted external IdP, verified redirect URI)
    BFF / session service (HttpOnly Secure SameSite session cookie)
           |
    L7 load balancer (health, SSE no response buffering, graceful drain)
           |
       +---+--------------------------+
       |                              |
    Control API replicas          Go Gateway replicas
       |                              |
       |                           model router
       |                              |
       |                       GPU/CPU inference pools
       |                           (vLLM or Ollama)
       |
    PostgreSQL (authoritative)
       - users and OIDC subject mappings
       - org/project membership and grants
       - approved model revisions and deployments
       - benchmark/report metadata, append-only audit entries
       |
    Redis (ephemeral coordination; no durable authority)
       - shared atomic rate-limit counters and quota reservations
       - expiring OIDC authorization-code/PKCE state or BFF session lookup
       - short-lived, revocable access/session cache entries
       - optional job queue *notifications* (durable job state stays in PG)

Arrows above are a **proposed system topology**. A documented **opt-in loopback HAProxy configuration** now exists for two local Go gateway processes and is syntax-tested in CI; this is not a deployed/protected multi-host ingress. Redis shared account-mutation quota code also exists opt-in; a live Redis
coordinator, external IdP and GPU worker pool have NOT been provisioned
or tested by this milestone.

## Identity and sessions

1. Authenticate with an external, operator-trusted OpenID Provider (e.g.
   Keycloak or managed OIDC), using Authorization Code + PKCE (S256).
   Enforce issuer, audience, azp/client, signature, state, nonce,
   expiry, appropriate token type and exact redirect URI. Do not take
   signing URLs from JWT headers or assume an ID token is an API access token.
2. Prefer a backend-for-frontend (BFF) that keeps access/refresh tokens
   server-side. Browser receives an opaque, HttpOnly, Secure, SameSite cookie.
   Protect write routes with CSRF controls, strict Origin checks and
   request-size limits. Rotate session identifiers after login and
   privilege elevation; revoke sessions on logout. Define absolute/idle TTL.
3. Any displayed role or org selector is cosmetic. Every API authorization
   decision resolves issuer+subject against current **PostgreSQL membership**
   and the real server-resolved resource, with deny-by-default permissions.
   No trust in browser-supplied org IDs, JWT role claims or Redis cache
   entries as the sole source of truth. Revocation requires bounded
   freshness or explicit invalidation.
4. User profile identity is the pair (verified token issuer, verified subject).
   A profile does not itself create an org, role or deployment privilege.
   The new /api/v1/me endpoint is an authenticated **API profile slice**,
   not a registration/login page.

## PostgreSQL boundaries

- PostgreSQL is the source of truth for profiles, organizations, membership,
  grants, secrets metadata, deployments and benchmark history.
- Strong integrity via foreign keys, unique constraints, tenant scoping,
  check constraints, explicit migrations, transactions and parameter binding.
- Keep migration privileges separate from the runtime role; the FastAPI app
  never auto-migrates, auto-seeds admin users or provisions grants from tokens.
- For scale: choose bounded DB pool per process; calculate max connections as
  replicas × pool size; use a pooling proxy only after measuring contention.
  Define backup/restore tests, encryption, retention and deletion policies.
- Add PostgreSQL row-level security and per-tenant isolation tests before
  multi-tenant writes. Application-layer RBAC is not RLS by itself.
- Store artifact binaries in dedicated object storage, not in Redis or
  token/JWT metadata. Report metadata may be indexed in PostgreSQL.

## Redis design and failure policy

- Redis is **not** a primary SQL database, not a model registry and not an
  RBAC/tenant-membership oracle. Never store full user prompts, raw access
  tokens, refresh tokens or API secrets unencrypted in Redis.
- Use memory-limited instances, ACL users with least privilege, authentication,
  TLS in any networked topology, no public port, key prefix isolation and
  bounded TTL for every ephemeral key.
- **Implemented opt-in control API slice:** `METRIQEN_REDIS_URL` and a private HMAC secret enable one atomic Redis Lua quota for POST/PATCH `/api/v1/me` (20 mutations per 60-second first-hit window). Redis key uses HMAC(issuer,subject); no raw personal identifiers or access tokens in Redis. It fails closed (503) on Redis errors and rejects over-quota requests (429). This is NOT a browser session store, gateway quota, login rate limiter or real-Redis benchmark. The Redis endpoint is restricted to authenticated 127.0.0.1:6379/0 with an ACL username/password; no hosted Redis service exists by default.
- Use atomic Lua or Redis transactions for per-identity quotas/rate limiting;
  do not approximate a distributed quota by per-replica in-memory counters.
  Choose fail-closed on admission and privilege-sensitive mutations when
  Redis quota coordination is required but unavailable. For explicitly
  optional performance caches, degrade to authoritative SQL reads.
- Separate ephemeral web sessions from quota namespaces and queue control.
  Redis persistence/eviction policy depends on whether it holds revocable
  session state; set eviction policy deliberately and test restarts.
- Never claim distributed exactly-once execution from Redis pub/sub alone.
  Persist long-running deployment-job state in PostgreSQL; use durable
  acknowledgements/idempotency keys for any worker queue.

## Load-balancer / streaming / inference concerns

- Terminate HTTPS at a trusted ingress, only forward known proxy headers,
  block direct public access to Go inference backends and the operator console,
  enforce authentication and project quotas **before** dispatching inference.
- Non-streaming HTTP and SSE streaming have different timeout/buffering needs.
  Disable proxy buffering for SSE, preserve heartbeat/flush semantics and
  propagate disconnect cancellation to active model generation.
- A LB does not equal GPU batching. A global dispatcher needs actual replica
  capacity/model readiness, queue depth and cancellation information; naive
  round-robin can route to a model not loaded on a particular worker.
- Drain old connections on redeploy; preserve zero-downtime routing and
  protect against duplicate billing/usage accounting using idempotency IDs.
- No traffic to a candidate deployment until model artifact digest, serving
  readiness and policy checks pass. Gradual rollout/rollback state and
  quality gates must be durable and auditable in PostgreSQL.
- Metrics: successful requests/s, failure rate, p95 latency, TTFT, queue
  delay, token throughput, GPU utilization/VRAM and per-project quotas.
  Never present inferred or synthetic measurements as real speedups.

## Security review: principal risks and mitigations

| Threat | Control / required test |
| --- | --- |
| JWT forgery, wrong audience, ID token misuse | pinned asymmetric JWT verifier, negative claim/signature tests |
| Cross-tenant reads or writes | trusted membership lookup, composite foreign keys, RLS gates, negative tenant tests |
| Stale role after removal | authoritative DB lookup / bounded invalidation, same-token revocation tests |
| CSRF or token exfiltration | PKCE, state/nonce, BFF HttpOnly cookie, CSRF/Origin controls, secure headers |
| SSRF to metadata/remote LLMs | fixed approved upstream origins and deny-redirect/deny-proxy clients |
| Prompt leakage through logs or caches | redact by default, no model text in general telemetry or Redis |
| Unbounded Redis/gateway queue exhaustion | atomic quotas, bounds, timeout, backpressure, fail-closed |
| Load balancing to wrong model revision | immutable artifact identity and verified worker readiness |
| Redis/DB outage | explicit 503 and degraded state; never grant default access |
| Malicious admin or supply chain | least privilege, private secrets, PR checks, audits, signed/pinned artifacts |

## Concrete development order

1. **This milestone:** verified OIDC token → private PostgreSQL user profile
   create/read/update; real migration and cross-identity tests. No IdP/login UI.
2. **Next:** configured IdP/BFF Authorization Code+PKCE session flow,
   login/logout and actual account page; key rotation, revocation and CSRF tests.
3. Project/organization create/read/update workflows gated by authoritative
   RBAC and PostgreSQL constraints. Add typed audit records.
4. Extend the new Redis-backed account-mutation limiter into full BFF sessions and per-tenant inference quotas; add real Redis integration/outage tests
   and outage-mode verification; separate cache and durable stores.
5. Real traffic proof for the new loopback HAProxy two-replica config with SSE support, then protected
   ingress/load balancing, configuration and readiness checks.
6. Scoped gateway invocation, model deployment lifecycle, reliable jobs,
   benchmark history, adaptive optimization and validated GPU serving.

**Release gate:** Do not call this production-ready until verified end-to-end
sign-in, multi-tenant separation, Redis failure behavior, load-balanced
streaming and model worker security are tested in an isolated deployment.
