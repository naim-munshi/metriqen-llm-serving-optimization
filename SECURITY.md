# Security policy

**Current status: local-only engineering foundation. Not production-ready.**

## Report a vulnerability

Do not post credentials, exploit details, or private data in public issues. Use GitHub's private vulnerability reporting or security advisory flow if it is enabled by the maintainer.

## Current threat exposure

- Control-plane health routes are unauthenticated and return no credentials. The separate read-only `/api/v1/identity` route is fail-closed and requires a pinned-public-JWKS-verified dedicated access token when explicitly configured.
- Docker Compose publishes service ports to the host loopback interface only.
- Do not expose these services publicly or route production traffic to them.
- The readiness routes currently check process availability only; they do not validate external dependencies.
- Verified production identity-provider login, deployed PostgreSQL tenant persistence, production ingress and complete business-route RBAC, TLS termination, project rate limits and tamper-evident/retained audit are NOT implemented. Internal transactional team audit storage exists but is not an append-only, production-grade audit service. A narrow identity route and project-read RBAC seam exist; the latter is **disabled by default**, with an explicit loopback-only PostgreSQL opt-in for trusted storage. An isolated PostgreSQL 16 migration-backed CI job now exists; it is not a deployed database or IdP integration. The inference gateway now has an opt-in, loopback-only project authorization check; unconfigured legacy inference is still unauthenticated. Do not expose these services to a network.

## Opt-in local model management

The local model manager's single-turn chat API checks approved and loaded exact model digests and forwards only to the fixed loopback Go gateway. It never forwards the operator token as a gateway credential. The independently accessible Go inference endpoint can now require a separate OIDC bearer token and PostgreSQL-authorized project in explicit opt-in mode, but existing model-manager calls are not automatically integrated with that mode. The local model manager's static UI is enabled alongside the operator model routes; it ships restrictive CSP and never persists the entered operator token in browser storage. Its API operations still require the loopback caller and bearer token. The development-only FastAPI model registry endpoints are disabled unless both a local operator bearer token and an absolute SQLite path are configured. The routes verify loopback caller address and the token before consulting private local data or native Ollama. They do not extend the OIDC project identity boundary, enforce admission in the Go gateway, lock mutable Ollama tags at runtime, provision remote models or provide production deployment authorization. Never enable them beyond loopback or expose the local token.

## Authenticated user profile boundary

When both trusted OIDC verifier and local PostgreSQL are configured, `/api/v1/me` allows create/read/update **only** for the verified `(issuer,subject)`. Client-provided role, organization, profile ID or JWT role claims cannot select another account. The profile record grants no organization membership, API key or inference privilege; it stores no passwords. Without configuration, protected account access fails closed. An optional Redis Lua-backed limiter now restricts verified account profile mutations when configured with a local authenticated Redis ACL user and private HMAC secret; quota failure blocks writes, not permission evaluation. This is not a Redis-backed session coordinator or unauthenticated-login brute-force defense. An **optional localhost-only browser OIDC Code+PKCE login, Redis session and logout implementation exists** (see the browser-auth section below). A trusted external IdP has not been connected and accepted on the operator's Mac; **production** identity/session hardening or protected public ingress is not implemented. An optional HAProxy loopback listener routes POST chat between two local Go gateway replicas; it is not public TLS ingress and does not coordinate cross-replica quotas. Direct Go gateway authentication must be independently enabled on each replica in guarded mode. [Profile runbook](docs/operations/verified-user-profiles.md) and [security architecture](docs/architecture/identity-data-redis-load-balancing.md).

## Required before production use

Threat modeling; OIDC/MFA; server-side authorization; scoped, rotatable API keys; secret management; model-artifact verification; tenant-isolation tests; production-grade token and concurrent inference quotas and timeouts; logs without raw prompts; vulnerability scans; backup/restore and incident-response tests.

Never commit tokens, credentials, private prompts, or proprietary benchmark datasets.

## Authorization foundation (Phase 02)

A deny-by-default authorization policy and role/scope tests are available in services/control-api/app/security/policy.py. The project-read route integrates them with authenticated identity and a trusted project/membership resolver. A relational schema and SQLAlchemy adapter are available for controlled testing, but the default runtime has **no configured membership database**, so no project access is enabled unless the operator explicitly supplies a trusted local PostgreSQL DSN and pre-provisioned schema. The API exposes health, identity-inspection and a fail-closed project-read route, not general production CRUD or tenant provisioning. See docs/architecture/adr/0001-identity-and-authorization-boundary.md and docs/security/threat-model-v1.md.

## In-memory signing-key verification (Phase 03)

The security library can verify RS256 access tokens against a trusted **server-supplied** JWKS snapshot, requiring a unique key ID and expected calling client. It rejects token-supplied key URLs and enforces issuer/audience/time claims. There is no live JWKS retrieval or verified real-IdP operator login. Separately, an **opt-in localhost browser login and Redis session implementation** exists (below); this is not a production identity service. The new project-read route does enforce PROJECT_READ through a trusted lookup seam in tests, but the optional PostgreSQL adapter has been exercised against a disposable PostgreSQL 16 service in CI, but not deployed. See ADR-0007, [ADR-0008](docs/architecture/adr/0008-project-rbac-http-boundary.md) and the [local identity runbook](docs/operations/pinned-oidc-identity.md).

## Local-only inference bridge

Enabling `METRIQEN_UPSTREAM_URL` allows `POST /v1/chat/completions` only on a loopback-bound gateway. It restricts upstream origins to local development hosts, denies arbitrary client-selected URLs and does not forward Authorization/Cookie headers. It provides process-level concurrency limiting, not tenant quotas. **This is not production-grade authentication or secure multi-tenancy.** An optional gateway authorization mode now checks a per-request JWT and current SQL permissions through a pinned loopback control API. Its shared secret is not mTLS and legacy unguarded inference remains supported; neither may be exposed publicly. See [Local inference guide](docs/operations/local-inference.md).

## Optional browser OIDC authentication, development only

When an operator explicitly configures trusted HTTPS authorization/token endpoints under the same pinned OIDC issuer, a compatible IdP, and authenticated local Redis, Metriqen now exposes a browser OIDC Code+PKCE login flow, a one-time Redis state bound to a browser cookie, nonce-verified RSA ID token matched to a dedicated at+jwt access token, and revocable 30-minute Redis sessions. Profile writes and logout require CSRF + exact Origin checks. **This is a localhost HTTP development flow** with HttpOnly SameSite=Lax cookies and deliberately Secure=False. It is NOT suitable for public exposure; converting to HTTPS/host-only Secure cookies, upstream logout/revocation, trusted proxy policy, real IdP acceptance and tenant authorization are required. Missing auth configuration leaves the browser routes unavailable. See [configuration and limitations](docs/operations/browser-oidc-login.md).

## Organization / project self-service authorization

Authenticated users can create their own organizations, becoming owner through an atomic PostgreSQL transaction, not through JWT/browser role claims. Project creation requires current server-read organization owner/admin membership, with a row lock before the mutation; non-owner project listing is restricted to current project grants. Anonymous and cross-tenant writes are denied. Browser routes also require CSRF and exact Origin. This functionality is local/opt-in. Internal transactional invitations, verified membership management, and audit storage are merged. Cancelling a pending invitation uses a new operator-applied migration and blocks replay, but PostgreSQL row-level security and tamper-evident audit retention are NOT implemented. [Workspace data guide](docs/operations/workspace-data-management.md).

## Team membership HTTP/BFF boundary

The local-only `/api/v1/organizations/{org_id}/invitations` (including restricted pending listing and cancellation) and membership/audit APIs require a verified dedicated access JWT; the `/auth/` browser equivalents require a verified, Redis-revocable session and same-origin CSRF on writes. Every authorization decision is read from PostgreSQL under an organization transaction lock, not from an HTTP-supplied role or tenant ID. Invitation subject is exact and same-issuer, never email-address based. Default missing identity, browser session or team DB configuration fails closed. Audits share the membership transaction but are not append-only or independently retained. See [team runbook](docs/operations/team-invitations.md).

## Pending invitation cancellation (local-only)

A separately applied `0004_invitation_cancellation.sql` adds `cancelled_at` and audited `invitation.cancelled` events. Current owner/admin membership and role ceilings are rechecked under the organization's PostgreSQL row lock. Cancelled invitations disappear from pending lists, cannot later be accepted, and can be replaced; repeated, cross-tenant and higher-privilege cancellation attempts fail closed. Browser cancellation additionally requires the Redis session, exact Origin, and CSRF token. These protections have synthetic-identity unit/HTTP tests plus isolated PostgreSQL 16 CI, not a deployed environment test.

## Optional distributed project request-budget enforcement

When explicitly configured alongside guarded inference, Redis 7 Lua enforces
an atomic, first-hit 60-second **per-project request count** across gateway
and control API replicas sharing a Redis instance, HMAC key and limit. A
verified JWT and current SQL `inference.invoke` permission are checked
*before* touching a quota key. Keys contain no plaintext tenant or user
identifiers. Quota denials return HTTP 429 + fixed `Retry-After: 60` without
sending prompts to the runtime; Redis errors are generic 503. Legacy unguarded
gateway mode bypasses this protection; configuration must be consistent on
**every** replica and cannot be assumed operational from code alone. No
global in-flight limit, token/cost quota, retry refund, real provider,
live Redis validation on Mac, or production hardening is claimed.
[Operator runbook](docs/operations/project-inference-authorization.md).

## Opt-in per-project Go inference authorization

When both the control API's `METRIQEN_GATEWAY_INTERNAL_TOKEN` and gateway-side `METRIQEN_GATEWAY_PROJECT_AUTH_URL`/`METRIQEN_GATEWAY_PROJECT_AUTH_TOKEN` are operator-configured, direct chat inference is denied until an OIDC access JWT and a canonical project ID pass the current `inference.invoke` permission check on trusted PostgreSQL data. Untrusted JWT role/tenant claims are ignored; authorization happens before prompt decoding, queue admission or runtime forwarding. The gateway treats service errors and malformed decisions as 503 and never forwards credentials or the project header to Ollama. The legacy loopback-only mode stays unauthenticated unless guarded mode is explicitly enabled. Do not enable the legacy shared bearer token with project mode. The new unit/HTTP checks and disposable PostgreSQL CI tests are not real IdP/HTTPS, Mac or production load tests. See [project authorization runbook](docs/operations/project-inference-authorization.md).

## Process-local provider-reported inference usage telemetry

The local Go gateway's `GET /metrics/usage` endpoint records only
bounded aggregate **provider-reported**, internally consistent
`prompt_tokens` / `completion_tokens` / `total_tokens` for completed,
fully written non-streaming HTTP responses **and** clean, fully
forwarded SSE streams with an exact terminal `[DONE]` marker and
one bounded final `choices:[]` usage JSON event. Missing, malformed,
duplicate/late, inconsistent, oversized, or over-requested-cap usage
claims are separately counted but excluded from token totals.
Streams without clean EOF plus terminal marker, including
canceled/interrupted or post-terminal data, cannot contribute token
totals. This is only structurally plausible **provider self-reporting**,
not independent streaming tokenization or an attested runtime bill. No arbitrary response text, prompt,
project or user identifiers, raw model metadata or per-request labels enter
the process-local counters. The endpoint is read-only, no-store and must
remain on loopback. **Self-reported data is not cryptographically verified,
not actual/billable token usage and cannot settle or refund the conservative
Redis output-cap budget.** See
[provider usage observability](docs/operations/provider-usage-observability.md).

## Optional per-project requested output cap reservations

An additional **explicitly opt-in** Redis 7 token-cap budgeting seam can
reserve the fully validated request's `max_tokens` units atomically before
forwarding prompts to Ollama. It requires both Python
`METRIQEN_PROJECT_OUTPUT_RESERVATION_PER_MINUTE` (256–1,000,000) and
Go `METRIQEN_GATEWAY_PROJECT_OUTPUT_RESERVATIONS=1`, plus previously
configured shared Redis project in-flight leases. Admission requires a
live, one-use, random scoped permit; Redis `TIME`, `ZSCORE`,
`EXISTS` and atomic counter increments prevent cross-replica replay
or TOCTOU double debit. The requested cap is charged conservatively,
even for incomplete, canceled or failed inference requests; never
refund based on upstream self-reported token usage. Redis errors fail
closed. Mismatched controller/gateway opt-in rejects new inference.
Keys contain only tenant-scoped HMAC digests and opaque random permit
receipts. **This is not an actual generated token meter, input-token
measurement, cryptographic runtime enforcement, usage billing or
cost attribution**. Unprotected legacy loopback gateway routes bypass
the opt-in budget; an operator must configure all replicas consistently.
See [request output cap runbook](docs/operations/project-output-token-reservations.md).

## Optional distributed active-inference project permits

An independently configured `METRIQEN_PROJECT_INFLIGHT_*` Redis 7 ZSET
enforces an atomic per-project **active request limit** across guarded Go
gateway replicas. An authorization response includes an opaque, random
expiring lease only after fresh signed JWT/current database `inference.invoke`
authorization, optional strict SQL deployment readiness and request-rate
quota. Go requires the lease under
`METRIQEN_GATEWAY_PROJECT_INFLIGHT_LEASES=1`, and releases it after the
inference handler returns—including client cancellation and upstream error.
An authenticated, loopback-only internal release endpoint cannot release
arbitrary permits without an unguessable permit ID and the internal service
secret. Unexpected Redis responses or outages fail new inference closed.
A 240s lease TTL permits crash recovery after the gateway's nominal 150s
execution budget. This is **not cryptographic runtime attestation or a
guarantee that a wedged handler is killed when a permit expires**; a process
running past TTL could cause oversubscription. Partial Go/Python
configuration fails closed, but intentionally unguarded loopback gateways
still bypass limits. No global token-count, GPU, cost, or production TLS/IdP
enforcement is claimed. See
[distributed project permit runbook](docs/operations/distributed-project-inflight.md).

## Opt-in strict project-deployment model routing

With `METRIQEN_ENFORCE_DEPLOYMENT_ROUTING=1` configured on **every**
Go gateway and control API replica, a gateway inference request must name a
canonical deployment. After verified OIDC/SQL inference permission, control
API requires the deployment's desired running, same-generation ready
observation within 120s and returns a trusted tag/digest binding. Go compares
the validated chat JSON model against that binding and probes its pinned
Ollama `/api/ps` for an exact currently loaded digest before scheduling or
forwarding any prompt. Stopped, missing, cross-tenant, unready, stale, changed,
malformed or unavailable revisions fail closed. Trusted Redis request quotas
are enforced after SQL authorization. This protects local development from
simple forged model/deployment headers; **disabled strict mode still allows
the older project-only guard and legacy unguarded local inference**.
An operator must enable the strict flag consistently; it is never silently
turned on by setting only one side. No production live model revocation,
immutable/signed artifact attestation, mTLS, real identity provider testing,
or protection against local Ollama tag mutation between /api/ps and inference
is established. See [strict model-routing runbook](docs/operations/project-inference-authorization.md).

## Explicit local deployment reconciliation (snapshot only)

An opt-in operator-invoked reconciliation command now observes approved
Ollama model identities using a pinned loopback origin and allows bounded
preloading only after matching the operator's explicit tag/digest allowlist
and locally installed digest. PostgreSQL migration `0006` adds expiring
work leases, bounded attempts and fixed observed-state codes. Desired
generation changes invalidate previous observations and any old worker
lease; a stale worker cannot publish a false ready state. Runtime errors
are categorized, never persisted verbatim. **Ready is a point-in-time
loaded digest observation, not live health or project-specific serving.
Disabled never unloads globally shared models, nor blocks direct gateway
traffic.** Running the CLI and operator approval are separate mandatory
steps, not automatic server behavior. Ollama tag retagging between
preload checks remains a TOCTOU risk in the absence of immutable artifacts.
See [local reconciler runbook](docs/operations/deployment-reconciler.md).

## Durable desired deployment records (not model-serving orchestration)

An opt-in PostgreSQL desired-state API now uses verified dedicated OIDC
access JWTs, database-authoritative `deployment.read` /
`deployment.manage` roles, composite project/organization FKs,
serialization under project row locks, strict generation checks, and durable
issuer/subject-scoped idempotency receipts. It is **separate** from the
operator-only SQLite aliases. Changing desired state does not verify a model
digest, schedule GPU workers, load Ollama models, authorize serving of that
model, or assert health: all observed states remain `unreconciled`.
Operator migrations `0005` and `0006` are needed. The
optional, explicit local reconciliation command can record a bounded
observed status and preload approved existing models, but does not create
production reconciliation. The **separately opt-in** strict local
project-deployment routing mode checks SQL desired/observed readiness and
revalidates the Ollama loaded digest before inference, without providing
production-grade routing isolation. Model signature
attestation, secure browser editing, cross-component serving routes,
always-on health checks or audited immutable runtime events remain absent. See
[durable project deployment runbook](docs/operations/durable-project-deployment-state.md).
