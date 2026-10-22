# Google OIDC sign-in compatibility (local opt-in, not public release)

Metriqen's existing browser BFF deliberately required a signed `typ: at+jwt`
**dedicated Metriqen API access token** from a compatible OIDC IdP.
Google does **not** issue that token type for third-party Metriqen API usage.
Its authorization and token endpoints are on **different** hosts.
This optional compatibility path authenticates a Google identity with a
nonce-verified, operator-pinned **Google ID token** then creates the existing
opaque, revocable, Redis-backed localhost browser session. Google's OAuth
access token is **not used** as a Metriqen API credential or stored.

It does not change the existing JWT `/api/v1/...` contract. The Google
ID token is **rejected** if submitted as a Metriqen API access bearer.
Google sign-in does not grant any project, deployment, inference or admin
permission: server-side current PostgreSQL membership remains authoritative.

## What is implemented

- Explicit `METRIQEN_BROWSER_PROVIDER=google`, default remains
  `dedicated-jwt`.
- Exact allowlisted Google endpoints: no client-supplied endpoints, wildcard
  Google domains, redirects or proxy-env forwarding.
- OIDC Authorization Code + PKCE S256, Redis one-time state and cookie binding;
  `nonce`, issuer, ID-token signature, RS256, `aud`, `iat`, `exp` validated.
- Existing 30-minute opaque Redis sessions, CSRF, logout and PostgreSQL RBAC.
- Synthetic negative tests, including foreign token endpoint, wrong nonce,
  wrong issuer, login replay and blocked use of ID token for direct API auth.

**No live Google login was performed.** Existing trusted JWKS file pinning
requires manual Google key-rotation maintenance; absent/stale key IDs fail
closed. No automatic remote JWKS refresh has been added.

## Owner setup for localhost *only*

1. In Google Cloud Console, create/choose a project, configure the OAuth
   consent screen and a **Web application** OAuth client. Register the
   exact authorized redirect URI:

   `http://127.0.0.1:8000/auth/callback`

   Google must actually accept your chosen URI. If it does not, do not
   weaken the loopback Host/CSRF protections or register a public proxy.
   Follow the provider's verified supported redirect procedure instead.
2. Obtain a Google Web Client ID and **Client secret**. Never commit
   either secret, access/refresh/ID tokens, personal emails or private keys.
3. Retrieve and verify Google's current published **public JWKS** out of
   band from the Google discovery-document `jwks_uri`. Store the public
   JWKS snapshot under a protected private absolute path, and plan manual
   refresh during key rotation. No untrusted per-request key discovery.
4. Configure authenticated local PostgreSQL with migrations and
   authenticated local Redis according to the existing
   [OIDC browser runbook](browser-oidc-login.md).
5. Set these **illustrative** environment variables on the Mac:

```dotenv
METRIQEN_OIDC_ISSUER=https://accounts.google.com
METRIQEN_OIDC_AUDIENCE=metriqen-control-api
METRIQEN_OIDC_AUTHORIZED_PARTY=YOUR_GOOGLE_WEB_CLIENT_ID
METRIQEN_OIDC_JWKS_PATH=/private/absolute/google-public-jwks.json

METRIQEN_BROWSER_PROVIDER=google
METRIQEN_BROWSER_AUTH_URL=https://accounts.google.com/o/oauth2/v2/auth
METRIQEN_BROWSER_TOKEN_URL=https://oauth2.googleapis.com/token
METRIQEN_BROWSER_CLIENT_SECRET=YOUR_PRIVATE_WEB_CLIENT_SECRET
METRIQEN_BROWSER_REDIS_URL=redis://ACL_USER:LONG_RANDOM_PASSWORD@127.0.0.1:6379/0
```

The variables above do **not** substitute for a real deployed secure IdP,
PostgreSQL, Redis or successful Google OAuth application verification.

## Never publish the local mode

The existing `/auth/` browser routes use localhost-only peer and Host
validation, `http://127.0.0.1:8000` redirect and exact CSRF Origin
checking, and **non-Secure** development cookies. Google provider support
does not change that. Never expose it on a public network, reverse-proxy
it to the internet or treat a shared-secret Model Manager login as user auth.

**Still needed for public Google login:** a separately hardened
HTTPS-only origin, trusted reverse proxy handling, `Secure` `__Host-`
cookie policy, OAuth redirect registration on the real domain, scoped
client/session secret rotation, hosted Redis/PostgreSQL and backup/recovery,
provider key rotation, rate limits, real browser/E2E integration,
security review, and real identity provider consent.

Official Google references:
- https://developers.google.com/identity/openid-connect/reference
- https://developers.google.com/identity/openid-connect/openid-connect
