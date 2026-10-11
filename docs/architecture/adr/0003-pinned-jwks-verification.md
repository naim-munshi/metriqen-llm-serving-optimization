# ADR-0003: Pinned JWKS key selection for access-token verification

- Status: Accepted, partial implementation
- Date: 2026-10-12
- Scope: Control API authentication library only

## Context

A signing-key PEM stored statically cannot handle planned identity-provider key rotation without manual replacement. A token's unverified header can contain an attacker-selected key ID, a public key, or a URL. Accepting arbitrary token-supplied key material risks signature bypass and SSRF.

## Decision

1. Authenticate only against a trusted server-provided JWKS snapshot. No network fetch or token-header-provided key URL is allowed in this module.
2. Select an RSA key by exact unique kid. Require an RS256 token and key intended for verification. Reject private RSA key material, unknown or duplicate IDs, unexpected key algorithms, and keys under 2048 bits.
3. Require configured issuer with HTTPS scheme, configured audience and authorized party (azp) for this verifier. Validate signature and standard timing claims using the existing OIDC claim verifier.
4. Reject oversized tokens, embedded public-key headers, unsupported critical headers, and ambiguous multi-audience tokens.
5. Allow key rotation by replacing a trusted JWKS snapshot, then verifying tokens against approved key IDs.

## Verification

Offline tests sign tokens with ephemeral RSA keys, exercise key rotation, wrong signers, issuer/audience/azp mismatches, hostile headers, invalid key sets, and size limits. No external identity provider is contacted.

## Not yet implemented

JWKS HTTPS discovery, origin pinning, secure cache refresh, Keycloak integration, API middleware, persisted sessions, MFA, identity-to-membership mapping, runtime authorization, revocation, and login screens. This is not yet a working authentication system.

## Follow-up acceptance gates

- Implement issuer-pinned OIDC discovery and bounded JWKS caching with timeout, safe refresh and known-key retention during provider outage.
- Integration test a locally running Keycloak instance and its supported token profiles; explicitly distinguish API access tokens from ID tokens.
- Then implement protected API routes and database-backed tenant membership validation.
