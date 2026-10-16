# Verified workspace / project data management (local)

This milestone adds a real PostgreSQL organization/project creation and listing workflow. OIDC access tokens and signed-in browser sessions use verified issuer/subject identities. Authorization always comes from current PostgreSQL membership, never JWT role claims, browser fields or cookies.

## API contract

| Method | Route | Rule |
| --- | --- | --- |
| GET | /api/v1/organizations | Return organizations in which verified caller is currently a member |
| POST | /api/v1/organizations | Transactionally create organization and verified creator as its owner |
| GET | /api/v1/organizations/{id}/projects | Owners/admins see all; restricted roles see granted projects only |
| POST | /api/v1/organizations/{id}/projects | Requires persisted owner/admin membership and project.create permission |
| GET/POST | /auth/organizations | Browser session equivalent, with CSRF on writes |
| GET/POST | /auth/organizations/{id}/projects | Browser session equivalent, with CSRF on writes |

Names are 1–200 printable characters, with extra JSON fields rejected. Resource IDs are server-created UUIDs, and URL IDs must be canonical. An unknown org and an unauthorized org both return 404. Users cannot assign themselves arbitrary roles via JSON or signed JWT claims.

## Persistence

The existing 0001 tenant access migration supplies organizations, projects, organization_memberships and project_grants. The creator's owner membership and new organization are inserted atomically. Project creation reads a current membership under a row lock before inserting. The current local limits are 20 owner organizations per identity and 100 projects per organization; these application limits are not global billing quotas.

GET/POST are implemented; rename, delete, archive, invitations, owner transfer, audit logs and row-level security remain future work. The local operator model token grants no organization or project privileges.

## Browser user experience

After setting up an operator-approved real compatible OIDC provider, pinned public JWKS, local PostgreSQL and authenticated Redis, open:

http://127.0.0.1:8000/console/account

Sign in using the trusted identity provider. In Organizations & Projects create an organization, open its project list and create a project. The browser renders names using text nodes only. The server enforces authorization on every request; there is no direct browser database access.

## Verification and blockers

GitHub CI runs synthetic JWT/SQLite permission tests and an actual ephemeral PostgreSQL 16 migration-backed test for owner bootstrap, cross-tenant denial and project creation. Local browser tests use fake Redis sessions and fake RSA identities. No real external IdP, production database, Redis service or Mac browser acceptance has been verified for this milestone.

Production blockers: HTTPS Secure cookies, identity-provider revocation, trusted TLS ingress, full invitation and membership management, database-level tenant RLS, audit, distributed quotas on every mutation and Go gateway tenant authorization.

See docs/architecture/identity-data-redis-load-balancing.md and docs/operations/browser-oidc-login.md.
