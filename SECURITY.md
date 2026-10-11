# Security policy

**Current status: local-only engineering foundation. Not production-ready.**

## Report a vulnerability

Do not post credentials, exploit details, or private data in public issues. Use GitHub's private vulnerability reporting or security advisory flow if it is enabled by the maintainer.

## Current threat exposure

- Only unauthenticated liveness/readiness routes exist. They are deliberately simple and return no credentials.
- Docker Compose publishes service ports to the host loopback interface only.
- Do not expose these services publicly or route production traffic to them.
- The readiness routes currently check process availability only; they do not validate external dependencies.
- Live authentication, HTTP authorization enforcement, tenant persistence, TLS termination, project rate limits and audit logging are NOT implemented. A local-only inference bridge is available and unauthenticated; do not expose it to a network.

## Required before production use

Threat modeling; OIDC/MFA; server-side authorization; scoped, rotatable API keys; secret management; model-artifact verification; tenant-isolation tests; inference quotas and timeouts; logs without raw prompts; vulnerability scans; backup/restore and incident-response tests.

Never commit tokens, credentials, private prompts, or proprietary benchmark datasets.

## Authorization foundation (Phase 02)

A deny-by-default authorization policy and role/scope tests are available in services/control-api/app/security/policy.py. These are standalone domain primitives, **not runtime authentication or HTTP authorization**. The API still exposes only health routes. See docs/architecture/adr/0001-identity-and-authorization-boundary.md and docs/security/threat-model-v1.md.

## In-memory signing-key verification (Phase 03)

The security library can verify RS256 access tokens against a trusted **server-supplied** JWKS snapshot, requiring a unique key ID and expected calling client. It rejects token-supplied key URLs and enforces issuer/audience/time claims. There is no live JWKS retrieval, identity-provider login, session handling, or HTTP enforcement. See ADR-0003.

## Local-only inference bridge

Enabling `METRIQEN_UPSTREAM_URL` allows `POST /v1/chat/completions` only on a loopback-bound gateway. It restricts upstream origins to local development hosts, denies arbitrary client-selected URLs and does not forward Authorization/Cookie headers. It provides process-level concurrency limiting, not tenant quotas. **This is not production-grade authentication or secure multi-tenancy.** The route must not be exposed publicly. See [Local inference guide](docs/operations/local-inference.md).
