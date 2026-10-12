# ADR-0010: Fail-closed local PostgreSQL runtime opt-in

- Status: Development-only integration (real PostgreSQL acceptance pending)
- Date: 2026-10-13
- Scope: FastAPI control API, not the Go inference gateway

## Context

ADR-0009 introduced a SQLAlchemy Core tenant schema and read-only
ProjectAccessStore adapter, not connected at startup. The next milestone
is an opt-in local configuration without opening production attack surface.

## Decision

1. Reuse the existing signed-identity and project-RBAC HTTP contract. Only
   an operator-set METRIQEN_DATABASE_URL may opt in to the trusted store.
   Missing configuration leaves authenticated project requests disabled.
2. Accept only postgresql+psycopg URLs with an explicit database, username
   and loopback hostname (localhost, 127.0.0.1 or ::1). Reject remote hosts,
   unsupported drivers, URL query options, malformed and oversized DSNs.
   Never take database URLs from requests or JWTs.
3. Build the SQLAlchemy Engine and store at startup without connecting to
   the database. No schema migration, table creation or membership seeding
   occurs on import. Malformed explicit config aborts startup.
4. Bound the connection pool, connection timeout and statement timeout.
   Generic HTTP 503s on backend failures must not expose secrets or SQL.
   Process readiness is not a database connectivity check.
5. Preserve existing test-injected stores, identity verification and all
   existing read-only business API semantics.

## Verification

Offline tests cover loopback DSN validation, rejection of remote servers,
unsupported drivers, query-option injection and missing settings, as well
as lazy Engine construction and unchanged security contracts.

## Deferred

CI does not start PostgreSQL or execute the SQL migration. Current store
lookups use separate database connections for project and membership;
request-scoped transactional isolation, revocation races, provisioning,
DB read-only roles, TLS, monitoring and migration drift verification remain
separate acceptance gates. The Go gateway remains unauthenticated and must
not be exposed to any public network.
