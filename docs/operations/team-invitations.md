# Verified team membership storage (local-only, not exposed over HTTP)

This milestone introduces an internal, PostgreSQL-backed team invitation and
membership service. It is a storage/authorization seam, **not** a browser
feature, email invitation service or public API. Existing organization and
project HTTP routes do not yet expose invitation or membership mutations.

## Trust and permission rules

An invitation is addressed to the exact OIDC issuer and opaque subject,
not an email address, JWT role claim or bearer invite token. Only the verified
recipient can accept. Issuance requires a current owner/admin membership;
owners may invite admins and lower roles, admins may invite only lower roles.
No one may invite an owner, remove an owner or remove themselves. The inviter
must still have the necessary membership at acceptance time.

Acceptance is one-time and expires after seven days. The PostgreSQL
organization row is locked for issuance, acceptance and revocation. A duplicate
active invitation, existing membership, exhausted pending limit or expired
invitation is rejected. Revocation cascades existing project grants through
the original tenant schema.

Team audit rows are inserted **in the same transaction** as issuance,
acceptance and revocation; an audit write failure rolls back the mutation.
Audit reads require current database-granted AUDIT_READ permission. The
audit contains action, verified issuer/subject, role, organization and
timestamp; it contains no passwords, prompts, email addresses or tokens.

## Migration and local validation

Apply migrations 0001, 0002, then 0003 through an operator-controlled
PostgreSQL migration process. Never apply DDL from an HTTP request or app
startup. CI runs synthetic identity and SQLite adversarial tests. A dedicated
PostgreSQL 16 CI job applies migrations 0001, 0002, and 0003 against an
ephemeral loopback database and exercises the invitation acceptance/audit
lifecycle. This is not verification of a user-managed PostgreSQL instance.

The application must remain loopback-only. No real external OIDC provider,
Redis session, Mac database, HTTPS Secure cookie or production deployment
has been verified. Before exposing a team API, add authenticated HTTP and
CSRF-protected browser adapters, invite cancellation, concurrency tests on
PostgreSQL, audit retention/append-only database privileges, and an explicit
operator acceptance procedure. The table is not yet tamper-evident; database
administrators can alter it, and deleting an organization cascades its events.
