# ADR-0008: Project-read HTTP RBAC contract with trusted server lookups

- Status: Accepted for local integration testing; persistent repository pending
- Date: 2026-10-13
- Scope: FastAPI control plane only

## Context

The control API can verify dedicated RS256 access tokens (ADR-0007) and has
a deny-by-default organization/project policy (ADR-0001), but no HTTP route
previously joined these pieces with an authoritative membership lookup.
Allowing JWT claims such as role, project_ids, or organization_id to drive
authorization would create cross-tenant access and stale-grant risks.

## Decision

1. Add only GET /api/v1/projects/{project_id}. Return a minimal read-only
   project record after JWT verification and explicit PROJECT_READ permission.
   There are no project mutations or inference permissions in this change.
2. The caller provides a project UUID, **not** the organization or member role.
   The server resolves the project and its organization from a trusted
   ProjectAccessStore interface, then loads membership by verified token issuer
   and subject plus the resolved organization. The existing policy performs
   the final project-scoped RBAC decision.
3. The store is injected from trusted backend composition, never selected by
   client-supplied URLs, request bodies, JWT organization claims or headers.
   The app's default startup has no such store: project reads return HTTP
   503 after authentication instead of using a fake or permissive database.
4. Unknown IDs, missing membership, forbidden role, unassigned project and
   cross-organization access all return the same generic HTTP 404 with no
   project metadata; malformed project IDs are also rejected.
5. Invalid, missing or forged access tokens retain the strict HTTP 401
   behavior. Store failures return generic HTTP 503, without error internals.
   No raw JWT, trusted secret or membership record is returned.
6. Lookup is performed on every request (no TTL cache or stale JWT grants).
   A production implementation will require transactionally consistent,
   bounded queries and explicit handling of membership revocation.

## Verification

Offline tests use ephemeral signing keys and an explicitly injected mutable
fake store. Test coverage includes owner/admin org-wide reads, assigned
project roles, cross-org isolation, token-claim forgery, different token
subjects, revoked membership, wrong project identity, invalid stored roles,
unknown/hidden resource indistinguishability, store outages and no writes.
The fake store is **only** a test implementation, not production storage.

## Limitations and follow-up

There is **no PostgreSQL persistence or runtime wiring**, so the new route
remains unavailable by default. There is no functional organization/project
creation API, login flow, authorization cache, audit log or inference API-key
enforcement. In particular, the Go gateway remains unauthenticated and
loopback-only. Do not deploy this service publicly.

Next: implement PostgreSQL-backed projects, memberships and assignments
with migrations and transactional revocation; test tenant isolation against
real storage, audit access/denial without raw prompts, and verify RBAC
semantics in authenticated business APIs. Do not enable this endpoint with
an unverified source of project or membership data.
