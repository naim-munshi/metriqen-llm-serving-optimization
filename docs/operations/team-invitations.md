# Local verified team invitations, membership and audit

This milestone exposes the existing transactional PostgreSQL team store through
two **opt-in local-only** API surfaces. Neither surface accepts caller-supplied
issuer, role claims, organization membership, or invitation bearer tokens.
An invitation targets an **exact opaque OIDC subject** under the authenticated
inviter's pinned issuer, not an email address. There is no email delivery.

## API contracts

Dedicated signed at+jwt access tokens verified against operator-pinned public
JWKS authorize these bearer endpoints when both OIDC verification and the
trusted local PostgreSQL store are configured:

- `GET /api/v1/invitations` – current recipient's pending invitations
- `POST /api/v1/invitations/{id}/accept` – exact recipient, once only
- `POST /api/v1/organizations/{org_id}/invitations` – JSON `{"target_subject":"...","role":"viewer"}`, owner/admin only
- `GET /api/v1/organizations/{org_id}/members` – owner/admin only
- `POST /api/v1/organizations/{org_id}/members/revoke` – JSON `{"target_subject":"..."}`, owner/admin only
- `GET /api/v1/organizations/{org_id}/audit` – role with `audit.read`

The equivalent opt-in browser BFF routes use `/auth/invitations` and
`/auth/organizations/{org_id}/...`. Every browser mutation needs an active,
revocable Redis session, exact `Origin: http://127.0.0.1:8000`,
`Sec-Fetch-Site: same-origin` if supplied, and `X-Metriqen-CSRF` obtained
from the same-origin `/auth/session` endpoint. They do **not** accept access
tokens from browser JavaScript. `/console/account` provides invitation
acceptance and owner/admin team management. It builds identity labels using
DOM `textContent`, never arbitrary HTML.

Inputs are strictly validated, unknown/unauthorized tenants return the same
404, and datastore failures return generic 503 responses. Organization
membership changes and audit inserts are in the **same transaction**. Owners
can invite admins or lower roles; admins can invite lower roles only.
Owner invitations, owner removal, self removal, admin-on-admin removal,
duplicate pending invitations, replay, cross-tenant access, and removed-inviter
acceptance are denied. No approval or role is granted by a JWT custom claim.

## Migration and operator acceptance

1. Keep FastAPI, Go gateway, PostgreSQL and Redis on **loopback only**. Never
   forward port 8000 or 8081 publicly.
2. Review and explicitly apply migrations `0001_tenant_access.sql`,
   `0002_verified_user_profiles.sql`, `0003_team_invitations_audit.sql`
   **in that order** using a privileged operator migration role and backups.
   Application startup and HTTP requests do not perform DDL.
3. Configure operator-pinned IdP issuer/audience/client identifier/public JWKS,
   HTTPS IdP authorization/token URLs, and local authenticated Redis ACL
   following `docs/operations/browser-oidc-login.md`. Keep DSNs, secrets and
   cookies private; avoid exposing local test identities to an external IdP.
4. Manually test two **real** IdP identities only after an IdP and Redis exist:
   sign in as owner, invite the other identity's exact `sub`; sign in as
   recipient and accept; verify replay fails; revoke as owner; verify access
   loss on the next request and audit entry creation.
5. Verify CSRF and origin denials in a local browser, Redis outage behavior,
   expired sessions, duplicate invitations and removed owners/admins.

CI exercises synthetic OIDC/Redis, SQLite policy tests and isolated
PostgreSQL 16 migrations and transaction tests. It does **not** attest a
user's Mac, real OIDC login, production HTTPS Secure cookies, or deployed
PostgreSQL/Redis. Missing configuration leaves protected routes unavailable.

## Explicit limitations

These are development-only HTTP cookies (`Secure=False`) restricted to
localhost, **not** hardened HTTPS BFF ingress. No email notifications,
invitation cancellation, owner transfer, project grant UI, per-project Go
gateway enforcement, append-only audit DB role, retention controls,
tamper-evident audit trail, PostgreSQL row-level security, or verified
production identity provider. DB administrators can alter audit rows and
organization deletion cascades their events. Before production, implement
TLS termination, `__Host-` Secure cookies, trusted proxy policy, upstream
logout, lifecycle monitoring, audit retention/privilege separation,
PostgreSQL concurrency tests, per-project gateway enforcement and deployment
verification.
