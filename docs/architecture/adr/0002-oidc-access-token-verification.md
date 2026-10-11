# ADR-0002: Verify access tokens before loading memberships

- Status: Partial implementation
- Date: 2026-10-12

## Decision

Access-token verification is isolated from organization authorization. The verifier accepts a public signing key only from trusted server configuration and checks RS256 signature, issuer, exact audience, subject, expiration, issued-at, and not-before claims. Default clock leeway is 30 seconds; caller may reduce it to zero. Multi-audience tokens are denied until authorized-party (azp) handling is explicitly designed.

Never derive trusted verification keys from token-supplied jku, x5u or arbitrary JWKS endpoints. Future JWKS resolution must use issuer-pinned HTTPS discovery, cache and key rotation rules, and reject unknown key IDs.

After authentication, project permissions must be loaded from trusted database memberships, never inferred from unverified identity claims.

## Limitations

- This PR adds a verification function and unit tests, **not** a Keycloak login, authenticated API, middleware, MFA, persistent memberships or session management.
- The public key is injected explicitly. Dynamic JWKS discovery/rotation will be implemented and integration-tested separately.
- Production integration must reject ID tokens when API access tokens are expected, including token-use/client authorization rules from the selected identity provider.
- Token verification failure never exposes an HTTP auth endpoint in this milestone.
