# ADR-0009: Tenant access relational schema and read-only SQL adapter

- Status: Accepted for schema development and offline integration testing
- Date: 2026-10-13
- Target runtime: PostgreSQL 15+, SQLAlchemy Core 2.x

## Context

ADR-0008 introduced a fail-closed HTTP project-read authorization boundary.
It depends on a trusted server-resolved project and organization membership
repository, but there was no relational schema or database adapter.
Persisting organization identifiers or roles in client JWT claims is unsafe
because those claims can be forged, stale or superseded by membership revocation.

## Decision

1. Introduce explicit tables for organizations, projects, organization
   memberships and project grants. No user, role, API-key or seed data are
   inserted by schema creation.
2. Membership identity is (issuer, subject, organization_id), derived from
   verified access-token identity and the **server-resolved** project
   organization. A user with the same subject in another identity issuer
   does not inherit memberships.
3. A project grant has *two* composite foreign keys: to the exact
   organization membership and to the project ID **with the same
   organization ID**. Database integrity must reject cross-tenant grants.
4. Use a constrained RBAC role allowlist at the database level and the
   already-tested policy function for per-request authorization. Organization
   owner/admin roles can read any project in their organization; other roles
   require a project grant.
5. Implement a SQLAlchemy Core read-only adapter conforming to
   ProjectAccessStore. SQL parameter binding, fresh requests and proper
   resource typing avoid text interpolation and stale JWT grants.
6. Keep migrations an explicit privileged operator action. Merely importing
   this package or starting the FastAPI app must **not** create tables,
   seed memberships or accept database credentials from HTTP requests.
7. The application still starts with no ProjectAccessStore by default and
   the project route returns 503 after valid authentication. Wiring to a
   configured PostgreSQL instance is a separate gated milestone.

## Verification

SQLite in-memory fixtures enable composite foreign keys explicitly. Tests
exercise database constraint enforcement, request-level RBAC, cross-tenant
denial, issuer/subject isolation, role updates, membership/grant revocation,
parameter-bound SQL and sanitized storage failures. Ephemeral RSA keys sign
test JWTs. CI never claims to execute the PostgreSQL migration.

## Limitations

- PostgreSQL 15+ DDL is provided but not executed in GitHub Actions.
  SQLite consistency tests are **not** PostgreSQL integration tests.
- Read transactions currently use the backend's isolation behavior.
  Full concurrent revocation guarantees, role-change transactions,
  race handling, tenant row-level security and durable audit are not proven.
- No production database credentials, migrations runner, secure PostgreSQL
  networking, backup/restore or secret management exists yet.
- No tenant CRUD or inference authentication is enabled. Go gateway stays
  loopback-only and unauthenticated.

## Next gates

1. Run PostgreSQL migrations in a disposable controlled database; add
   integration tests with real PostgreSQL FK/transaction semantics.
2. Design engine configuration with secret handling, TLS, bounded pools,
   health checks and fail-closed startup. Wire only with authenticated
   membership lookup and suitable audit logging.
3. Add write-side authorization, owner transfer, membership revocation,
   migration compatibility and tenant-row-isolation tests.
4. Implement scoped inference keys and resource quotas independently before
   any public serving route is permitted.
