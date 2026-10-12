# ADR-0007: Fail-closed pinned-JWKS HTTP identity boundary

- Status: Accepted for local development identity inspection only
- Date: 2026-10-13
- Scope: FastAPI control plane, not the Go inference gateway

## Context

The control API has a tested RS256/JWKS verifier and scoped authorization
policy, but the security modules were previously standalone functions.
The platform needs its first integration with an HTTP request without
pretending that tenant membership, identity-provider login, or a database
has already been implemented.

## Decision

1. Add only the read-only GET /api/v1/identity endpoint. It returns a signed
   token's verified subject, issuer and audience. It grants no organization,
   project or inference permissions.
2. Read a public JWKS snapshot from an operator-supplied local file once
   when the app is imported. Require explicit HTTPS issuer, audience, and
   authorized-party client ID. Never fetch keys from network endpoints or
   request/token-supplied URLs.
3. Reject partially configured identity settings at process startup. If no
   identity configuration is supplied, retain existing health endpoints but
   return 503 from the protected route. There is no permissive fallback.
4. Reject missing/duplicate/malformed Authorization headers. Use a strict
   single Bearer token size limit and verify its RS256 signature, unique kid,
   required time claims, issuer, audience and authorized party with the
   previously tested pinned-JWKS verifier.
5. To avoid treating ambiguous ID tokens as API access tokens, this first
   HTTP boundary requires an explicit typ=at+jwt. This is a deliberate
   profile restriction; some identity providers do not issue this JWT type
   by default. They are not supported until separately evaluated.
6. Never trust role, organization or project claims to authorize anything.
   No business operation is exposed, and no policy check is invented without
   a trusted server-side membership repository.
7. Respond with generic 401 and a Bearer challenge on rejection, never
   echoing the JWT, its payload, trusted key or decoding error. Preserve
   the existing Cache-Control: no-store response header.

## Verification

Offline HTTP integration tests use ephemeral RSA signing keys; they cover
valid identity, hostile claims, wrong signers, wrong issuer/audience/azp,
expired/not-yet-valid tokens, generic JWT types, duplicate headers,
JWKS snapshot rotation, missing/partial config and invalid key files.
Tests make no calls to an external identity provider.

## Security constraints

This is not a complete authentication or authorization platform. It has
no Keycloak login, IdP discovery, JWKS refresh, account provisioning,
persistent memberships, RBAC-backed business endpoints, revocation,
sessions, API-key management, audit storage, or gateway enforcement.
The Docker configuration stays loopback-bound. Do not expose services
publicly or infer production-readiness from this endpoint.

## Next gates

- Integrate a real identity provider with issuer-pinned discovery, robust key
  rotation, token profile tests and reliable fail-closed refresh behavior.
- Add trusted organization/project membership persistence and deny-by-default
  RBAC checks at the resource layer before implementing business endpoints.
- Secure inference traffic independently with scoped service credentials,
  quota admission, audit and tenant isolation tests.
