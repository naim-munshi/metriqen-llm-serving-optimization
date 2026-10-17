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

When both trusted OIDC verifier and local PostgreSQL are configured, `/api/v1/me` allows create/read/update **only** for the verified `(issuer,subject)`. Client-provided role, organization, profile ID or JWT role claims cannot select another account. The profile record grants no organization membership, API key or inference privilege; it stores no passwords. Without configuration, protected account access fails closed. An optional Redis Lua-backed limiter now restricts verified account profile mutations when configured with a local authenticated Redis ACL user and private HMAC secret; quota failure blocks writes, not permission evaluation. This is not a Redis-backed session coordinator or unauthenticated-login brute-force defense. There is still no actual browser OIDC login/session/logout, trusted IdP integration or **production** load-balanced protected ingress. An optional HAProxy loopback listener routes POST chat between two local Go gateway replicas; it is not public TLS ingress and does not coordinate cross-replica quotas. Direct Go gateway authentication must be independently enabled on each replica in guarded mode. [Profile runbook](docs/operations/verified-user-profiles.md) and [security architecture](docs/architecture/identity-data-redis-load-balancing.md).

## Required before production use

Threat modeling; OIDC/MFA; server-side authorization; scoped, rotatable API keys; secret management; model-artifact verification; tenant-isolation tests; inference quotas and timeouts; logs without raw prompts; vulnerability scans; backup/restore and incident-response tests.

Never commit tokens, credentials, private prompts, or proprietary benchmark datasets.

## Authorization foundation (Phase 02)

A deny-by-default authorization policy and role/scope tests are available in services/control-api/app/security/policy.py. The project-read route integrates them with authenticated identity and a trusted project/membership resolver. A relational schema and SQLAlchemy adapter are available for controlled testing, but the default runtime has **no configured membership database**, so no project access is enabled unless the operator explicitly supplies a trusted local PostgreSQL DSN and pre-provisioned schema. The API exposes health, identity-inspection and a fail-closed project-read route, not general production CRUD or tenant provisioning. See docs/architecture/adr/0001-identity-and-authorization-boundary.md and docs/security/threat-model-v1.md.

## In-memory signing-key verification (Phase 03)

The security library can verify RS256 access tokens against a trusted **server-supplied** JWKS snapshot, requiring a unique key ID and expected calling client. It rejects token-supplied key URLs and enforces issuer/audience/time claims. There is no live JWKS retrieval, identity-provider login or session handling. The new project-read route does enforce PROJECT_READ through a trusted lookup seam in tests, but the optional PostgreSQL adapter has been exercised against a disposable PostgreSQL 16 service in CI, but not deployed. See ADR-0007, [ADR-0008](docs/architecture/adr/0008-project-rbac-http-boundary.md) and the [local identity runbook](docs/operations/pinned-oidc-identity.md).

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

## Opt-in per-project Go inference authorization

When both the control API's `METRIQEN_GATEWAY_INTERNAL_TOKEN` and gateway-side `METRIQEN_GATEWAY_PROJECT_AUTH_URL`/`METRIQEN_GATEWAY_PROJECT_AUTH_TOKEN` are operator-configured, direct chat inference is denied until an OIDC access JWT and a canonical project ID pass the current `inference.invoke` permission check on trusted PostgreSQL data. Untrusted JWT role/tenant claims are ignored; authorization happens before prompt decoding, queue admission or runtime forwarding. The gateway treats service errors and malformed decisions as 503 and never forwards credentials or the project header to Ollama. The legacy loopback-only mode stays unauthenticated unless guarded mode is explicitly enabled. Do not enable the legacy shared bearer token with project mode. The new unit/HTTP checks and disposable PostgreSQL CI tests are not real IdP/HTTPS, Mac or production load tests. See [project authorization runbook](docs/operations/project-inference-authorization.md).
