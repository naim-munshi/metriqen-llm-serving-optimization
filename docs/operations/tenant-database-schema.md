# Tenant relational storage foundation (development-only)

The control API contains a PostgreSQL-compatible initial tenant access
schema and a SQLAlchemy Core adapter. The default app does not connect to
any database and does not automatically create or seed tables.

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

No database engine is created in app.main. The control app's project-read
endpoint still returns 503 when a trusted store has not been explicitly
injected. No write API, migrations automation, PostgreSQL availability,
signed key discovery, row-level security policy, audit trail or user login
is included. Inference traffic still has no authentication; maintain the
loopback-only boundary.

The next engineering milestone is a genuine PostgreSQL integration suite,
migration validation, secret-backed connection settings, reliable startup
and request-scoped transaction semantics. Treat this version as a tested
storage contract, not production tenancy.
