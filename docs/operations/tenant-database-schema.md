# Tenant relational storage foundation (development-only)

The control API contains a PostgreSQL-compatible tenant access schema,
SQLAlchemy Core adapter and an **opt-in local runtime configuration**. Without
the database environment variable, the default app does not connect to any
database. No configuration creates or seeds tables automatically.

## Files and trust boundary

- app/storage/schema.py: table definitions and tenant FK constraints
- app/storage/project_access.py: read-only ProjectAccessStore adapter
- migrations/0001_tenant_access.sql: first PostgreSQL schema migration
- tests/test_sql_project_store.py: SQLite-backed constraint / HTTP tests

Use only verified identity (issuer, subject) and project-to-organization
relationships fetched from trusted data. Never provide a client-supplied
organization ID, role or grant as a membership source. The adapter does
not inspect browser-controlled JWT authorization claims.

## Schema model

Organizations contain projects. An organization membership is keyed by
issuer + subject + organization. Project-scoped roles require grants.
Each grant references both the exact membership and the **same-organization**
project via composite foreign keys. Delete cascades revoke grants when an
associated membership or project is removed.

A role database CHECK constraint allows only owner, admin, ml_engineer,
deployment_operator, developer, auditor and viewer.

## Controlled migration

The migration in migrations/0001_tenant_access.sql is designed for
PostgreSQL 15+ and must be reviewed and applied once in a disposable,
access-controlled database by a privileged migration operator **before**
enabling any service queries. It is not automatically applied by the web
process. Do not commit PostgreSQL credentials or use a publicly exposed
database. Plan versioned migration history and rollback/backups before
production deployments.

The SQLAlchemy Core definitions allow SQLite-backed offline tests.
They do not prove that the PostgreSQL DDL is deployed or that actual
PostgreSQL transaction isolation is correct.

## Test locally without PostgreSQL

From services/control-api with dependencies installed:

~~~sh
python -m pytest -q tests/test_sql_project_store.py
~~~

CI creates ephemeral in-memory SQLite tables and enables foreign keys.
It tests authorized project reads, cross-organization grant rejection,
identity-based membership lookup, role changes, grant/member revocation
and no database error leakage to the HTTP API.

## Production gating

By default, no database engine is created. If the operator deliberately sets
METRIQEN_DATABASE_URL to a loopback PostgreSQL psycopg DSN and has already
applied the schema and provisioned legitimate roles, startup builds the
trusted adapter **without opening a DB connection**. Project requests then
open connections with a three-second connect timeout and PostgreSQL
statement timeout; missing/failed data returns generic 503. The project-read
endpoint returns 503 when no trusted store is configured. No write API, migrations automation, PostgreSQL availability,
signed key discovery, row-level security policy, audit trail or user login
is included. Inference traffic still has no authentication; maintain the
loopback-only boundary.

The next engineering milestone is a genuine PostgreSQL integration suite,
migration validation, secret-backed connection settings, reliable startup
and request-scoped transaction semantics. Treat this version as a tested
storage contract, not production tenancy.

## Runtime local connection (opt-in)

The database URL must be held outside Git and contain a trusted loopback host
(127.0.0.1, localhost, or ::1) and use the exact `postgresql+psycopg` driver.
A sample **placeholder**, not a credential to use unchanged:

~~~sh
export METRIQEN_DATABASE_URL='postgresql+psycopg://local_reader:REPLACE_WITH_SECRET@127.0.0.1:5432/metriqen_dev'
~~~

Configure the four pinned OIDC variables separately before starting the
local control API. Supply the real database password through a secure local
secret source. This adapter does not implement privileged CRUD, migrations,
audit logging, TLS termination or DB-availability readiness. Use a least-
privileged **SELECT-only** account; do not connect it to an internet host.

This is only runtime configuration wiring, **not** a live PostgreSQL CI test.
All CI database integration currently exercises SQLite fixtures and a fake
project store. A deployment must still perform a real PostgreSQL end-to-end
test and review revocation/transaction isolation under concurrency.
