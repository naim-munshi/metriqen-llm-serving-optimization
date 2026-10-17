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

## Optional distributed fixed-window project request quota

An operator can enable a **single shared Redis 7 authorization budget** in the
control API after PostgreSQL RBAC has verified `inference.invoke`. Without
this explicit configuration there is **no distributed inference quota**, even
when project-scoped gateway authorization is enabled. Set on *every* control
API replica, using identical values and the **same Redis datastore**:

- `METRIQEN_PROJECT_QUOTA_REDIS_URL`: authenticated ACL URL
  `redis://quota_user:<private-password>@127.0.0.1:6379/0` (local host only).
  Use an ACL identity restricted to the `metriqen:project-inference:v1:*`
  namespace with `EVAL`, `GET`, `INCR`, `EXPIRE` permissions. Do not use
  the default unauthenticated Redis user for a deployed system.
- `METRIQEN_PROJECT_QUOTA_HMAC_SECRET`: independent high-entropy random
  secret, 32–512 bytes, stored privately and **identical across replicas**.
  It is **not** the gateway internal token or account quota hashing secret.
- `METRIQEN_PROJECT_QUOTA_REQUESTS_PER_MINUTE`: optional strict positive
  integer 1–1000, default **60**. Configure the same value everywhere.

A Redis Lua script atomically checks the current counter, admits at most the
configured number of verified requests, and sets a 60-second TTL on the
first admitted attempt. This is a *first-hit anchored fixed window* (not
sliding window); denied requests do not extend TTL or increment counters.
Each organization+project key is a keyed HMAC; Redis sees no raw project,
subject, issuer, organization ID, prompt or model. The quota is per **project
across all users and gateway replicas**, not per JWT identity. Disabled
or incomplete quota settings cannot silently create a local-only counter:
partial settings fail startup, Redis errors return generic HTTP 503, and
unauthorized JWTs/permissions never consume a unit.

When the project quota is reached, Control API returns HTTP **429** and
`Retry-After: 60`. The Go gateway preserves a **bounded** 429 with this fixed
retry hint and does not read the prompt, enter its local scheduler, or
forward to Ollama. A successfully authorized attempt is charged even if
its chat JSON is later invalid, the gateway local queue rejects it, or
Ollama fails; there is **no retry reimbursement**. This is an *attempt-rate*
budget, **not** distributed in-flight concurrency, token metering, model
memory allocation, monetary billing, GPU scheduling or a production SLA.
Users may send a burst up to the configured limit at a window boundary.

**Critical deployment condition:** All protected Go replicas must use the
guarded project authorization mode, and all control API replicas must point
to the same Redis, key derivation secret and limit. If any replica remains
in legacy unguarded mode, inference can bypass the quota entirely. Do not
assume load-balancer affinity prevents bypass. Separate local account
mutation quotas remain independent and use their own environment settings.

CI includes race/concurrent synthetic gateway checks and real Redis 7 Lua
tests with a synthetic ephemeral ACL identity and expiry test; neither is a
production IdP/Redis/Mac validation nor a real GPU load benchmark.

## Strict project-deployment model routing (explicit operator opt-in)

**This is a separately enabled local development mode**, extending the
per-project authorization and Redis request budget. It is not on by default.
**Both** the Go inference gateway and Python control API must have the
following exact flag in their own process environments:

`METRIQEN_ENFORCE_DEPLOYMENT_ROUTING=1`

This flag refuses startup if the required project-auth secret, trusted OIDC
identity, or SQL deployment routing store is absent. Both processes still
require the project service credential and pinned localhost control API
URL from above. A deployment must exist in PostgreSQL after migrations
`0001` through `0006`; the desired state must be `running` and an
operator-invoked reconciler must have observed `ready` for the *same*
generation, digest and revision within the last **120 seconds**. The
reconciler may refresh previously ready observations at most once every
60 seconds when the operator runs repeated passes. A single manual
reconciliation pass is not an always-on health monitor.

**Trusted local client request example**, using actual OIDC bearer JWTs
and real operator-owned IDs (placeholders shown):

```sh
curl -fsS http://127.0.0.1:8081/v1/chat/completions \\
  -H "Authorization: Bearer ${REAL_OIDC_ACCESS_TOKEN}" \\
  -H "X-Metriqen-Project-ID: ${AUTHORIZED_PROJECT_UUID}" \\
  -H "X-Metriqen-Deployment: stable" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"llama3.2:1b","messages":[{"role":"user","content":"Hello"}],"stream":false}'
```

The gateway first sends *only* project UUID, deployment name and user JWT
to the pinned local control API. PostgreSQL resolves current issuer/subject
membership, `inference.invoke` project grants, deployment name,
desired/observed state, generation, model tag/digest and observation age
under shared row locks. Unknown/cross-tenant grants/deployments are denied
without leaking their existence. Stopped, failed, stale or unreconciled
deployments are denied HTTP **409**. The service response binds a specific
project+deployment+model tag+digest+generation. The Go gateway rejects
a missing/duplicate/invalid deployment header, a forged JSON body model,
inconsistent control replies or any other attempted legacy fallback.

After the signed control decision, Go validates the chat JSON model
matches the SQL-authorized tag. It then performs a bounded **GET
/api/ps** on the same operator-pinned Ollama origin used for chat, requiring
a *loaded* model whose digest matches the trusted SQL revision. A missing
or changed loaded revision is HTTP **409**, and a malformed, unavailable,
duplicate or oversized local model status fails closed with **503**.
Only after these checks does the prompt enter the local scheduler or travel
to Ollama. No JWT, project or deployment header is forwarded to Ollama.
This intentionally costs one bounded local model-status call per request.
Project quota counts previously authorized attempts, including downstream
model-status failures; it does not refund attempts.

**Deployment operator requirements:** do not mix strict and nonstrict
replicas behind HAProxy. All Go replicas must enable the strict flag; all
control API replicas must do the same and use the same PostgreSQL/Redis,
JWKS configuration, service token and state migrations. Even with strict
routing this is **not immutable model artifact attestation**: an operator
with control of local Ollama can retag a model between /api/ps and inference
(TOCTOU). SQL row locking blocks concurrent desired-state updates during
the authorization transaction, but a new stop/revoke after that decision
cannot retroactively cancel already-authorized or in-flight inference.
Only an isolated runtime, immutable signed digests, short-lived execution
capabilities and periodic state reconciliation can address these remaining
production risks. Neither Mac Ollama nor real IdP/TLS/GPU testing is
claimed by synthetic Go/SQLite and disposable PostgreSQL CI.

**Legacy compatibility:** when this flag is disabled, the old loopback-only
project permission mode remains unchanged, and does **not** claim
deployment-level isolation. To enforce deployment isolation across your
environment, enable the flag on every replica and prevent bypass through
legacy gateways.

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
peers, an audited API-key lifecycle, production-grade cross-replica admission and token quotas, model
deployment permissions, full component integration/load testing, audit
retention, backup/restore and incident response remain unfinished. The
local model manager may continue using the **legacy** gateway; it does not
automatically provide the project JWT/header when guarded mode is enabled.
