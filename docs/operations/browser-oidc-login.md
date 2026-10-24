# Local OIDC browser sign-in and Redis-backed sessions

**Status:** opt-in development authentication milestone; never public ingress.
This is a real Authorization Code + PKCE, nonce/state-validated OIDC login
implementation. The integrated login provider is **not bundled**. Without
a trusted configured HTTPS IdP, actual browser sign-in cannot be exercised.

## What users get

With fully configured identity and Redis, open:
**http://127.0.0.1:8000/console/account**

- "Sign in with identity provider" redirects the browser to a pinned HTTPS
  OIDC authorization endpoint using Authorization Code and S256 PKCE.
- A random 300s Redis one-time state, browser-bound HttpOnly flow cookie and
  signed nonce protect the callback from login CSRF/replay/substitution.
- The backend exchanges the authorization code at the pinned HTTPS token
  endpoint (no redirect or outbound proxy), verifies the RS256 ID token
  against the **operator-pinned public JWKS**, validates nonce/issuer/client
  audience and separately validates a dedicated \`at+jwt\` API access token
  against audience/issuer/azp, requiring identical subjects.
- On verified login, the backend issues a new random opaque local session
  cookie. Redis stores only its verified issuer, subject and CSRF secret
  (no ID/access/refresh tokens or password). Absolute idle-independent
  session lifetime: 30 minutes.
- \`GET /auth/session\` reports signed-in state and a same-origin CSRF value.
  \`GET /auth/profile\`, \`POST /auth/profile\`, \`PATCH /auth/profile\` use the
  session principal to read/write the same authoritative PostgreSQL profiles
  used by OIDC-access-token \`/api/v1/me\`. Writes require exact local Origin,
  \`Sec-Fetch-Site: same-origin\` when present, and a CSRF request header.
  Existing optional Redis account-write quotas apply on both API surfaces.
- \`POST /auth/logout\` deletes the Redis session and clears the browser cookie.
  A repeated/expired callback cannot create a new session, and a revoked
  session immediately loses access.

## Session outage/recovery integration evidence (CI only)

The isolated GitHub Actions Redis 7 job now exercises the **real** Redis
`GETDEL`, `SET NX EX`, `GET`, and `DEL` commands with a scoped synthetic
ACL account across two client instances. It tests single-use OIDC flow
state, session revocation across replicas, TTL expiry, namespace isolation,
and an intentionally unavailable **separate localhost transport** that
must fail with a generic HTTP 503. A healthy connection is restored and
previously revoked sessions remain invalid. The control API's synthetic
BFF HTTP test also checks that an outage cannot authorize a workspace
request or silently complete a logout.

Run only with the repository's disposable GitHub CI service. These tests do
**not** restart the actual operator's Redis, use real sessions/OIDC
credentials, verify a mobile browser's cookie policy, or attest live
PostgreSQL/IdP recovery. Real Mac recovery and Google login remain P5
operator-only acceptance tasks.

## Google sign-in compatibility (explicit local opt-in)

A separate `METRIQEN_BROWSER_PROVIDER=google` option now accepts Google's
**verified ID token** for browser-session creation; it does not treat
Google OAuth access tokens as Metriqen API JWTs. Google's pinned token
endpoint belongs to another Google-owned host, and exact endpoint
allowlisting is enforced. The existing dedicated `at+jwt` identity
provider mode remains the default. See [Google localhost setup and security
limits](google-oidc-local.md). **This is not a hosted Google-login launch.**

## Exact configuration

Everything remains **disabled by default**, with no extra pages or auth
routes when browser config is absent. An incomplete configuration aborts
startup. No real user is seeded.

First configure the existing **local** PostgreSQL and explicitly apply
\`0001_tenant_access.sql\` and \`0002_verified_user_profiles.sql\` through
your trusted migration procedure; the app never auto-migrates. Configure
the existing pinned JWT identity verifier:

    METRIQEN_OIDC_ISSUER=https://idp.example.org/realms/metriqen
    METRIQEN_OIDC_AUDIENCE=metriqen-control-api
    METRIQEN_OIDC_AUTHORIZED_PARTY=metriqen-web
    METRIQEN_OIDC_JWKS_PATH=/private/absolute/pinned-public-jwks.json

These are **illustrative placeholders**, not a valid provider configuration.
Use real issuer URLs and trusted public signing keys only; never commit
access tokens, secrets, private signing keys or database passwords.

Also configure (names must match your *real* IdP's endpoint paths):

    METRIQEN_BROWSER_AUTH_URL=https://idp.example.org/realms/metriqen/protocol/openid-connect/auth
    METRIQEN_BROWSER_TOKEN_URL=https://idp.example.org/realms/metriqen/protocol/openid-connect/token
    METRIQEN_BROWSER_REDIS_URL=redis://session_user:PRIVATE_LONG_PASSWORD@127.0.0.1:6379/0
    # Optional, only if the IdP registered a confidential web client:
    # METRIQEN_BROWSER_CLIENT_SECRET=<private-confidential-client-secret>

The authorization and token URL must be HTTPS paths under exactly the same
scheme/host/port as the pinned issuer. The local Redis server must use
**authentication with an ACL username/password**, database 0 and
127.0.0.1:6379. It must allow \`GET\`, \`GETDEL\`, \`SET\` with NX/EX and
\`DEL\` on the \`metriqen:browser:v1:\` namespace. Restrict its bind, ACL,
persistence access and logs; Redis is **not** automatically deployed.

Register the callback URI EXACTLY with the IdP:

    http://127.0.0.1:8000/auth/callback

Register the correct OIDC client ID \`metriqen-web\` (or your actual configured
\`METRIQEN_OIDC_AUTHORIZED_PARTY\`). The provider must issue **both**:

1. An ID token: RS256, trusted JWKS key ID, \`iss\`, client \`aud\`, \`sub\`,
   \`exp\`, \`iat\`, and correct PKCE login \`nonce\`.
2. An **API access token** (not a browser ID token): RS256, token header
   \`typ: at+jwt\`, \`iss\`, API \`aud\`, client \`azp\`, \`sub\`,
   \`exp\`, \`iat\`, \`nbf\`. The subjects must match. Some common
   providers do not issue \`at+jwt\` by default; configure an appropriate
   JWT access token profile or keep this integration disabled until tested.

The trusted JWKS snapshot is loaded at startup. Refresh/rotation currently
requires a trusted operator update and restart. There is **no** dynamic
issuer discovery or user-controlled \`jwks_uri\` resolution.

Start FastAPI on **127.0.0.1:8000** only (not 0.0.0.0). Do not put this
development-mode cookie/session implementation behind an HTTPS public
reverse proxy without additional production security work.

## Security constraints and known limitations

- This development-only loopback HTTP implementation uses **HttpOnly,
  SameSite=Lax, Secure=False** cookies because it is restricted to
  \`http://127.0.0.1:8000\`. **Public production use is unsafe**: move to
  HTTPS-only \`__Host-\` Secure cookies with a trusted TLS ingress, a
  deliberate CORS/CSRF/trusted-proxy policy, session rotation and audit logs.
- No password signup, magic links, account recovery, MFA setup, IdP user
  administration, external IdP logout (single logout), or refresh-token
  rotation is implemented. The provider itself owns those workflows.
- Sessions are revocable in local Redis, limited to 30 minutes and do not
  refresh. IdP-side logout/revocation is **not** automatically reflected
  until session expiration; use BFF backchannel logout/revocation or
  shorter sessions before a production release. Redis outage fails closed.
- Existing \`/api/v1/me\` still accepts only correctly signed **access**
  JWTs, not cookies. The new BFF \`/auth/profile\` is the browser session
  interface. Local Model Manager's distinct operator token does not
  grant a user session, PostgreSQL membership or deployment permission.
- A verified login creates **no** organization membership or model
  inference privilege. Multi-tenant organization/project CRUD, protected
  Go gateway, real hosted deployment and production scaling remain separate.
- Login isn't available merely because the UI route exists in source.
  A compatible external IdP, explicit trusted keys, real Redis and real
  PostgreSQL must be configured and acceptance-tested.

## Verification

Synthetic CI signs isolated test JWTs with ephemeral RSA keys, simulates
Redis atomic consume and revocation, checks replay prevention, cookie
properties, PKCE SHA-256 challenge, CSRF/Origin, invalid nonce, issuer
or subject, and PostgreSQL-profile adapter contracts. These checks
**do not** demonstrate a real external IdP working on your Mac.

See [target security architecture](../architecture/identity-data-redis-load-balancing.md)
and [verified profile DB runbook](verified-user-profiles.md).
