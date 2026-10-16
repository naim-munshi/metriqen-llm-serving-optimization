# Verified user profile API (local PostgreSQL)

This is a **functional authenticated user-data slice**, not a complete
signup/login system. The existing FastAPI server validates RS256 **access**
JWTs from an operator-pinned external OIDC issuer/public JWKS. It does
**not** issue access tokens, accept passwords, redirect to an IdP, set a
browser login cookie or implement session/logout. Do not expose it publicly.

## Scope and API

The profile is uniquely keyed by a verified pair \`issuer + subject\` and
stores only a bounded display name and UTC timestamps. It stores no raw JWTs,
passwords, PII email field, IDP metadata, roles, org grants or model prompts.

| HTTP | Path | Operation |
| --- | --- | --- |
| GET | /api/v1/me | Read current authenticated user's profile; 404 if absent |
| POST | /api/v1/me | Self-provision one profile from a verified access token; 201 |
| PATCH | /api/v1/me | Change only your own display_name; 200 |

POST/PATCH JSON: \`{"display_name":"Example User"}\` (trimmed,
1–120 printable characters, no extra fields). Account ID, role, organization,
issuer and subject are **never accepted from JSON or query strings**.
Duplicate POST returns 409; absent PATCH returns 404. Invalid or missing
JWT returns 401 when a verifier exists; unconfigured verifier/database
returns 503. Storage errors are sanitized and never echo DSNs or credentials.
The application never auto-creates a profile on GET.

## Required explicit setup

1. Provision a **local only** PostgreSQL database with a dedicated runtime
   user and a separate privileged migration user.
2. Apply checked-in migrations **in order**:
   \`services/control-api/migrations/0001_tenant_access.sql\` then
   \`services/control-api/migrations/0002_verified_user_profiles.sql\`.
   No production database or operator-managed database is modified by CI.
3. Configure the existing \`METRIQEN_DATABASE_URL\` using
   \`postgresql+psycopg\` on localhost. A URL for a remote database,
   an arbitrary query parameter or alternative driver is rejected.
4. Configure *all four* existing pinned OIDC variables:
   \`METRIQEN_OIDC_ISSUER\`, \`METRIQEN_OIDC_AUDIENCE\`,
   \`METRIQEN_OIDC_AUTHORIZED_PARTY\`, and \`METRIQEN_OIDC_JWKS_PATH\`.
   The JWKS file must contain operator-approved **public** keys only,
   never private signing material. No dynamic JWKS fetch occurs.
5. Start the FastAPI control API bound to \`127.0.0.1:8000\`. Supply a
   short-lived, correctly signed access token from that IdP only in an
   \`Authorization: Bearer\` header; never print or paste real access tokens
   into GitHub issues, chat, benchmark artifacts or screenshots.

The local Model Manager operator token is unrelated and **cannot access**
the \`/api/v1/me\` user routes without a valid OIDC access JWT.

## Verification

- Offline SQLAlchemy SQLite fixtures exercise issuer+subject isolation,
  no-cross-user mutation, strict profile inputs, role-claim spoofing,
  expired/invalid/ID-token rejection, persistence, duplicate conflict and
  DB failure sanitization. SQLite is **not** deployment persistence.
- The disposable PostgreSQL 16 CI database applies both real migrations and
  tests identical-issuer profile isolation, duplicate rejection and database
  constraints. CI does not connect to your Mac or external identity provider.

## Redis and load balancing

See [security and topology design](../architecture/identity-data-redis-load-balancing.md).
Redis session/quota storage, an external identity provider, BFF login
callbacks and multiple serving replicas are **planned**, not activated.
PostgreSQL remains the authoritative user/tenant store. No Redis key may
grant a membership or substitute a verified access token.


## Optional Redis-backed account-write quota

A first distributed Redis integration now exists. It is **disabled by
default**. When both env vars are present, the control API enforces one
atomic, per-verified-identity limit of **20 POST/PATCH /api/v1/me mutations
per 60-second first-hit window**. Reads remain unaffected. Redis counters
are shared between replicas using a Lua INCR + first-hit EXPIRE operation.
Requests over the limit return HTTP 429 and Retry-After: 60; if Redis is
unreachable/returns invalid data, mutation fails closed with HTTP 503.
Profile permissions still come ONLY from verified tokens and PostgreSQL.

Configuration (private env; **never commit the actual secrets**):

    METRIQEN_REDIS_URL=redis://YOUR_ACL_USER:LONG_SECRET@127.0.0.1:6379/0
    METRIQEN_RATE_LIMIT_HMAC_SECRET=<independent-random-secret-at-least-32-bytes>

Both settings must be set together or application startup fails. Only
127.0.0.1:6379, Redis DB 0, an ACL username and password (minimum 16
characters) are accepted. Remote and unauthenticated Redis endpoints
are rejected. The Redis instance is **not** provisioned by this
configuration; operators must set it up securely and separately. Never
paste credentials in chat, logs or screenshots. Store secrets outside
the repository, use restrictive permissions, rotate deliberately, and
disable Redis TCP access beyond loopback.

The HMAC secret deliberately prevents reversible low-entropy account IDs
in Redis key names. All quotas reset when the secret rotates. Disabling
Redis removes the shared quota (suitable for isolated local development,
**not** for a public or distributed deployment). Live Redis execution
and multi-instance correctness still require operator acceptance tests.
This is not a login-brute-force defense for unauthenticated traffic;
an authenticated, trusted ingress must enforce that separately.
