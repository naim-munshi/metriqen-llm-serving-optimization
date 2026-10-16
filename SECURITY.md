# Security policy

**Current status: local-only engineering foundation. Not production-ready.**

## Report a vulnerability

Do not post credentials, exploit details, or private data in public issues. Use GitHub's private vulnerability reporting or security advisory flow if it is enabled by the maintainer.

## Current threat exposure

- Control-plane health routes are unauthenticated and return no credentials. The separate read-only `/api/v1/identity` route is fail-closed and requires a pinned-public-JWKS-verified dedicated access token when explicitly configured.
- Docker Compose publishes service ports to the host loopback interface only.
- Do not expose these services publicly or route production traffic to them.
- The readiness routes currently check process availability only; they do not validate external dependencies.
- Full identity-provider login, deployed PostgreSQL tenant persistence, production business-route RBAC, TLS termination, project rate limits and audit logging are NOT implemented. A narrow identity route and project-read RBAC seam exist; the latter is **disabled by default**, with an explicit loopback-only PostgreSQL opt-in for trusted storage. An isolated PostgreSQL 16 migration-backed CI job now exists; it is not a deployed database or IdP integration. The inference gateway remains unauthenticated; do not expose these services to a network.

## Opt-in local model management

The local model manager's single-turn chat API checks approved and loaded exact model digests and forwards only to the fixed loopback Go gateway. It never forwards the operator token as a gateway credential and does not secure the independently accessible Go inference endpoint. The local model manager's static UI is enabled alongside the operator model routes; it ships restrictive CSP and never persists the entered operator token in browser storage. Its API operations still require the loopback caller and bearer token. The development-only FastAPI model registry endpoints are disabled unless both a local operator bearer token and an absolute SQLite path are configured. The routes verify loopback caller address and the token before consulting private local data or native Ollama. They do not extend the OIDC project identity boundary, enforce admission in the Go gateway, lock mutable Ollama tags at runtime, provision remote models or provide production deployment authorization. Never enable them beyond loopback or expose the local token.

## Authenticated user profile boundary

When both trusted OIDC verifier and local PostgreSQL are configured, `/api/v1/me` allows create/read/update **only** for the verified `(issuer,subject)`. Client-provided role, organization, profile ID or JWT role claims cannot select another account. The profile record grants no organization membership, API key or inference privilege; it stores no passwords. Without configuration, protected account access fails closed. An optional Redis Lua-backed limiter now restricts verified account profile mutations when configured with a local authenticated Redis ACL user and private HMAC secret; quota failure blocks writes, not permission evaluation. This is not a Redis-backed session coordinator or unauthenticated-login brute-force defense. There is still no actual browser OIDC login/session/logout, trusted IdP integration or **production** load-balanced protected ingress. An optional HAProxy loopback listener now routes only POST chat between two local Go gateway replicas; it does not authorize direct Go gateway requests, serve as public TLS ingress, or coordinate cross-replica quotas. [Profile runbook](docs/operations/verified-user-profiles.md) and [security architecture](docs/architecture/identity-data-redis-load-balancing.md).

## Required before production use

Threat modeling; OIDC/MFA; server-side authorization; scoped, rotatable API keys; secret management; model-artifact verification; tenant-isolation tests; inference quotas and timeouts; logs without raw prompts; vulnerability scans; backup/restore and incident-response tests.

Never commit tokens, credentials, private prompts, or proprietary benchmark datasets.

## Authorization foundation (Phase 02)

A deny-by-default authorization policy and role/scope tests are available in services/control-api/app/security/policy.py. The project-read route integrates them with authenticated identity and a trusted project/membership resolver. A relational schema and SQLAlchemy adapter are available for controlled testing, but the default runtime has **no configured membership database**, so no project access is enabled unless the operator explicitly supplies a trusted local PostgreSQL DSN and pre-provisioned schema. The API exposes health, identity-inspection and a fail-closed project-read route, not general production CRUD or tenant provisioning. See docs/architecture/adr/0001-identity-and-authorization-boundary.md and docs/security/threat-model-v1.md.

## In-memory signing-key verification (Phase 03)

The security library can verify RS256 access tokens against a trusted **server-supplied** JWKS snapshot, requiring a unique key ID and expected calling client. It rejects token-supplied key URLs and enforces issuer/audience/time claims. There is no live JWKS retrieval, identity-provider login or session handling. The new project-read route does enforce PROJECT_READ through a trusted lookup seam in tests, but the optional PostgreSQL adapter has been exercised against a disposable PostgreSQL 16 service in CI, but not deployed. See ADR-0007, [ADR-0008](docs/architecture/adr/0008-project-rbac-http-boundary.md) and the [local identity runbook](docs/operations/pinned-oidc-identity.md).

## Local-only inference bridge

Enabling `METRIQEN_UPSTREAM_URL` allows `POST /v1/chat/completions` only on a loopback-bound gateway. It restricts upstream origins to local development hosts, denies arbitrary client-selected URLs and does not forward Authorization/Cookie headers. It provides process-level concurrency limiting, not tenant quotas. **This is not production-grade authentication or secure multi-tenancy.** The route must not be exposed publicly. See [Local inference guide](docs/operations/local-inference.md).

## Optional browser OIDC authentication, development only

When an operator explicitly configures trusted HTTPS authorization/token endpoints under the same pinned OIDC issuer, a compatible IdP, and authenticated local Redis, Metriqen now exposes a browser OIDC Code+PKCE login flow, a one-time Redis state bound to a browser cookie, nonce-verified RSA ID token matched to a dedicated at+jwt access token, and revocable 30-minute Redis sessions. Profile writes and logout require CSRF + exact Origin checks. **This is a localhost HTTP development flow** with HttpOnly SameSite=Lax cookies and deliberately Secure=False. It is NOT suitable for public exposure; converting to HTTPS/host-only Secure cookies, upstream logout/revocation, trusted proxy policy, real IdP acceptance and tenant authorization are required. Missing auth configuration leaves the browser routes unavailable. See [configuration and limitations](docs/operations/browser-oidc-login.md).
