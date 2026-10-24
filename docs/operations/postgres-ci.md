# Ephemeral PostgreSQL CI verification

This runbook describes a **real PostgreSQL 16 migration-backed integration
suite** for the Metriqen FastAPI project authorization boundary. It is a
development/CI milestone, not a deployed membership service.

## Continuous integration

The GitHub Actions job `postgres-integration-tests` starts the isolated
`postgres:16.6` Docker service (synthetic CI credentials, database
`metriqen_ci`) and installs the existing Python control API package.
Then it executes:

~~~sh
cd services/control-api
python -m pytest -q tests/test_postgres_integration.py
~~~

The job alone supplies `METRIQEN_POSTGRES_CI=1` and a
`METRIQEN_TEST_POSTGRES_URL` pointing to the disposable loopback
`metriqen_ci` database. The test fixture **fails closed** unless the
driver, loopback host/port, database name and CI username match precisely.
With CI disabled the test module skips: regular Python CI and developer
machines do not silently start a database.

The fixture applies the **exact checked-in**
`services/control-api/migrations/0001_tenant_access.sql` migration
using a separate privileged psycopg connection with explicit SQL
`BEGIN` / `COMMIT`. It does not silently use SQLAlchemy
`metadata.create_all` as a substitute for the migration.

The migration-backed integration suite verifies:

- PostgreSQL tables, composite foreign keys, unique constraints and
  role check constraint from the checked-in migration.
- Valid signed access JWT + server-persisted role/grant returns the
  authorized project while an unassigned project remains hidden.
- Cross-organization project lookup remains indistinguishable from an
  unknown project despite forged JWT role, organization and grant claims.
- PostgreSQL rejects wrong-tenant composite grant FKs, missing
  membership FKs and forbidden role values.
- Grant addition/removal, role update and membership deletion affect
  the next authorized HTTP request; cascade deletion revokes grants.
- Missing/invalid JWTs return HTTP 401 without exposing database
  metadata in the response.
- An injected PostgreSQL store-read transport exception **after valid JWT
  verification** returns only a generic HTTP 503, never a private DSN or
  a cached authorization. Once the injection ends, the real CI PostgreSQL
  permissions are checked again, including cross-tenant denials. This
  simulates a connection error; it **does not** restart the server or prove
  operational failover/backups on the owner's Mac.

The job runs **beside**, rather than replacing, the SQLite-backed
contract suite, Go race tests, model-free E2E, local benchmark and NLP
evaluation checks. The isolated PostgreSQL container disappears with
the CI job and stores no user credentials or application data.

## Security boundaries

- The test suite is intentionally scoped to a named, disposable local
  CI database. It is **not** a general-purpose migration runner and
  must not be given a production or personal database DSN.
- Committed sample PostgreSQL CI credentials are intentionally
  **synthetic and non-production**. Do not reuse them anywhere else.
- The runtime read adapter remains opt-in and loopback-only; default
  application import does not run SQL migrations or seed identities.
- Test JWTs use a locally generated ephemeral RSA key and synthetic
  accounts. They are not proof of integration with a live identity
  provider.
- The tests do not establish safe online migrations, production backup
  / restore, real OIDC login, concurrent tenant transactional
  isolation, row-level security, deployment security or integration
  of control-plane identity with the Go inference Gateway.

### Developer note

Running the ordinary test suite does not require PostgreSQL. This
migration-backed suite runs only within the tightly restricted CI setup.
Its purpose is to detect differences between PostgreSQL-native SQL
constraints and earlier SQLite-based contract fixtures.

See [the project evidence matrix](../PROJECT_STATUS.md) and
[engineering portfolio briefing](../PORTFOLIO.md).
