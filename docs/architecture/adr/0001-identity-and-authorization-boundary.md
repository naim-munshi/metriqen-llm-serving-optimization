# ADR-0001: Identity and authorization boundaries

- Status: Accepted for architecture; implementation pending
- Date: 2026-10-12
- Scope: Metriqen control plane, not the inference serving runtime

## Context

Metriqen will expose organization-scoped administrative operations, project-scoped deployments and benchmarks, and inference endpoints. Administering a GPU cluster must not automatically imply access to tenant content. Client-provided organization IDs or unverified JWT role claims must never authorize access.

## Decision

1. Use an OpenID Connect identity provider (Keycloak is the proposed provider) with Authorization Code + PKCE for browser authentication, secure server-side sessions, MFA for privileged accounts, and OIDC discovery/JWKS validation in the backend.
2. Store organization memberships and project grants in Metriqen's PostgreSQL database, not solely in identity-provider tokens. Resolve them server-side for each authorized operation; cache only with explicit invalidation/short lifetimes.
3. Implement **deny-by-default**, scoped role-based access control. An organization owner or organization admin has access across its own projects. Other roles require explicit project assignment.
4. Treat platform operators as a separate infrastructure identity with no implicit cross-tenant prompt/model-data access; audited break-glass access requires an independent future design.
5. Inference API keys will have distinct project-bound identity/scopes, hash-at-rest storage, expiry and rotation. They cannot be treated as console login sessions.
6. Validate authorization on each protected backend route **and** on resources fetched by ID, including worker and data access. Never rely on hidden UI controls.
7. Require audit records for login/security policy changes, member invitations/role changes, key creation/revocation, benchmark artifacts, and deployment approvals. Do not log raw prompts by default.

## Current implementation

The pure-Python authorization policy in services/control-api/app/security/policy.py provides the initial permission map and scope checks. It is **not connected to HTTP endpoints, login, JWT validation, database membership, or audit events**. No user/admin login exists in this change.

## Alternatives rejected

- Custom password/credential system: increases attack surface.
- Trusting roles/tenant IDs from the browser or JWT alone: membership changes may not be reflected and untrusted claims can bypass server-side access rules.
- A global super-admin role that bypasses tenant checks: risk of cross-tenant data access.

## Follow-up gates

- Threat-model review, OIDC integration, persistent membership repository, lifecycle and revocation tests.
- Negative tests for user-to-organization crossover and object-level resource checks in real API endpoints.
- Scope the inference gateway independently; default deny until authenticated service credentials are enforced.
