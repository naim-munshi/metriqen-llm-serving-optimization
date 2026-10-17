# Project-scoped inference authorization (loopback development)

**Status: opt-in, not production-ready.** This milestone connects the Go
gateway to an independently verified, PostgreSQL-authoritative control API
decision. It protects direct `POST /v1/chat/completions` calls **only when
the project-auth mode is configured**. The legacy loopback mode remains
available for existing unmodified benchmarks, and is unauthenticated by default.
Never expose either development mode to the public Internet.

## Security contract

A caller supplies a dedicated, valid OIDC `at+jwt` access token in
`Authorization: Bearer ...` and the canonical project UUID in
`X-Metriqen-Project-ID`. A client-supplied JWT `role`,
`permissions`, `organization_id` or project-grant claim is ignored.
The Go gateway does **not** accept a client-provided authorization URL.

For every inference request, *before decoding the prompt or entering the
scheduler*, the Go gateway makes a brief `POST` to its operator-pinned
`http://127.0.0.1:8000/internal/gateway/authorize` with only:

- exact project UUID in JSON (no prompt/model content);
- caller's bearer access token for fresh signature and subject verification;
- a separate, private service-to-service token in
  `X-Metriqen-Gateway-Secret`.

Control API first checks local peer address and service token. It then checks
the signed user's exact issuer/subject, resolves the requested project from
PostgreSQL (not from JWT project claims), and consults current org membership
plus project grants under the `inference.invoke` permission. Owner/admin
may invoke any project *within their organization*; developer and ML engineer
need a current explicit project grant. Viewer, auditor and deployment operator
do not have inference.invoke merely because they have project.read. Missing
resources/denied grants return 404 at the control API. Go maps this to a generic
403; a malformed or untrusted authorization response and network/timeouts
fail closed at HTTP 503. This mechanism never forwards user credentials,
service secret, or project headers to Ollama. No authorization results are
cached, so a revoked grant is checked on the next call.

The Go side demands an exact `{"authorized":true,"project_id":"..."}`
response, matching the requested project, with JSON content type and no
unknown fields/trailing content. The authorization service is pinned to a
numeric loopback address; redirects, network proxies and credential-bearing
URLs are disallowed. A 2.5-second check timeout prevents stalled inference
admission. This is not mTLS; a compromised local process remains in threat
scope. No model-to-project binding, shared quotas or production auth exists.

## Local operator setup (explicit opt-in)

1. Keep both services **strictly on loopback**. Back up a user-owned
   PostgreSQL instance and explicitly apply existing operator SQL migrations
   `0001`, `0002`, `0003`, `0004` in order; no DDL occurs on startup.
2. Provision the user's OIDC issuer/client/JWKS and local database according
   to `pinned-oidc-identity.md` and `workspace-data-management.md`.
   Ensure the intended user has an owner/admin membership or a
   developer/ML-engineer project grant. Never use a client-controlled role.
3. Generate a distinct random service secret (32–256 ASCII printable
   characters, excluding quotes, commas and whitespace), store it in your
   local secret manager, and configure **the same private value** on:
   - Control API: `METRIQEN_GATEWAY_INTERNAL_TOKEN`
   - Go gateway: `METRIQEN_GATEWAY_PROJECT_AUTH_TOKEN`

   Do not commit or print this value or include it in curl output/logs.
   The existing `METRIQEN_GATEWAY_BEARER_TOKEN` is a legacy operator
   token, not an OIDC access token; **do not enable it at the same time**.
4. On the Go process set
   `METRIQEN_GATEWAY_PROJECT_AUTH_URL=http://127.0.0.1:8000/internal/gateway/authorize`
   and `METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434`. Run the
   control API on port 8000, Go on port 8081, Ollama on port 11434.
5. Make a locally authorized request to
   `http://127.0.0.1:8081/v1/chat/completions` with a dedicated access
   token, project ID, normal JSON chat payload and `Content-Type:
   application/json`. Confirm positive inference and then remove the
   database grant; the **same unexpired JWT must be denied** on the next
   request. Test a missing token/project ID, another organization's
   project, forged JWT roles, and control API/DB outage. Neither secret
   should appear in HTTP error bodies, metrics or upstream headers.

## Exact scope of CI evidence

- Go unit/HTTP tests use a **simulated** loopback control API to test
  authorization gating, rejection before prompt forwarding, unknown/bad
  results, duplicate headers, revocation and service outage.
- Python tests exercise synthetic issuer-signed JWTs and current trusted
  membership/roles. A dedicated PostgreSQL 16 CI test uses the real schema,
  migrations, grants and revocation. This is **not** live OIDC or full
  Go-to-FastAPI-to-PostgreSQL-to-Ollama deployment validation.

## Remaining blockers

The earlier legacy loopback mode is still available and must not be treated
as tenant isolation. Real IdP acceptance, TLS/`__Host-` Secure cookies,
local process and secret isolation, protection against compromised localhost
peers, an audited API-key lifecycle, distributed per-project quotas, model
deployment permissions, full component integration/load testing, audit
retention, backup/restore and incident response remain unfinished. The
local model manager may continue using the **legacy** gateway; it does not
automatically provide the project JWT/header when guarded mode is enabled.
