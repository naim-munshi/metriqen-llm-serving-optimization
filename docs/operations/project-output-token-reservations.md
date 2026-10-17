# Conservative per-project requested-output-cap budget (development opt-in)

**Scope:** a Redis-backed limit on *requested maximum output tokens per
minute*, not actual generated tokens, token usage reporting, model-side
enforcement, invoicing, or production-ready GPU admission scheduling.

The existing local gateway validates an integer `max_tokens` from 1 to 2048
(and inserts a default 256 when absent). This feature charges **that entire
maximum** once per authorized inference request, before it reaches Ollama.
Consequently a request with `max_tokens:2048` reserves 2048 units even if
the model emits 10 tokens or rejects the request. These reservations never
refund on failure, client cancellation or short completion. They cannot
measure prompt/input tokens or exact model billing and must not be sold as
such. The Ollama runtime is not cryptographically bound to honor this
requested generation ceiling; do not treat it as an absolute bound on
physical token generation.

## Enable explicitly and consistently on EVERY replica

Requires the existing signed JWT + PostgreSQL tenant authorization and
**opt-in distributed in-flight Redis permits** (the 240-second leases).
Follow [distributed project concurrency](distributed-project-inflight.md)
first. On the trusted Python control API process, set:

```sh
export METRIQEN_PROJECT_OUTPUT_RESERVATION_PER_MINUTE=8192
```

Allowed project cap: **256–1,000,000** requested output token units per
fixed 60-second window. Only this new setting is needed beyond the
existing private Redis ACL, project inference HMAC and live-lease
configuration. The budget shares the **same Redis connection and
authenticated 128-bit lease** as active request admission, not a client
supplied URL, identity, model or body. The ACL user needs access to
`metriqen:project-inflight:v1:*` **and**
`metriqen:project-output:v1:*`, plus script commands `EVAL`,
`TIME`, `ZSCORE`, `EXISTS`, `GET`, `INCRBY`, `EXPIRE` and
`SET` (as well as the previous in-flight commands). Use Redis AUTH ACL
credentials and a loopback-only port; never expose that user publicly.

On **each** guarded Go gateway process, also set:

```sh
export METRIQEN_GATEWAY_PROJECT_INFLIGHT_LEASES=1
export METRIQEN_GATEWAY_PROJECT_OUTPUT_RESERVATIONS=1
```

Missing or invalid startup configuration fails closed. The Go gateway
also fails closed when one side requires output reservations but the
other side does not; never mix replicas with inconsistent controls behind
HAProxy. The legacy unguarded local gateway does **not** enforce this
feature and must not be treated as a production route.

## Request flow and limits

1. Go requires a dedicated verified access JWT and a canonical project
   header and calls the pinned loopback control API. Python resolves
   authoritative current PostgreSQL project membership and permission;
   optional strict deployment-ready/digest checks and request/minute
   throttling remain in place.
2. With Redis active limits enabled, a random per-request 128-bit lease
   is atomically created. The control decision explicitly includes
   `output_reservation_required: true`. Go rejects mismatched config
   or a malformed/absent lease before touching inference.
3. Go fully validates the JSON request, its `max_tokens` and model.
   Existing strict model digest checks and local admission remain.
   **After local admission, before forwarding** to Ollama, Go posts only
   the authenticated opaque permit and the validated integer cap to
   `POST /internal/gateway/reserve-output`, using the pinned local
   service secret. No JWT, prompt or tenant ID is forwarded to this endpoint.
4. One Lua EVAL, using **Redis server TIME**, verifies that the exact
   in-flight permit is active and unexpired, rejects reused permits
   (one successful reservation at most), reads the per-project
   60-second counter and atomically charges the entire requested cap
   if it fits. Redis keys use the already HMAC-scoped tenant/project
   digest, plus random permit receipts; no plaintext identities or
   model text are stored in these keys. Receipts outlive their underlying
   permit even if a previous request released early.
5. Exceeded budget returns **HTTP 429** with a fixed `Retry-After:60`,
   without forwarding the prompt. Expired or replayed permits return
   **409**; Redis outage, ACL error or malformed control responses return
   generic **503**. The in-flight permit is released via the existing
   authenticated finally-equivalent gateway cleanup (including after
   errors and cancellations). Denied requests do not consume the token
   budget, though they may consume the separately configured
   *requests/minute* budget from the earlier authorization stage.

## Validation and limitations

CI uses isolated synthetic Go/Python tests for forwarded JSON equality,
duplicate JSON cap keys, invalid caps, replay, Redis outages, deliberate
upstream 5xx and consistent rejection across two simulated replicas.
Actual disposable Redis 7 integration creates 16 authenticated leases
across two independent clients and shows a 1024-unit cap accepts exactly
four 256-unit reservations while the other 12 are denied, without
refunds. Tests also check ACL namespaces, expired permits, one-time
receipt semantics and fixed-window reset.

Not validated: a real Mac/Ollama/GPU, live IdP, production networking,
Redis primary failover, accounting reconciliation, prompt tokens, final
emitted token counts, hardware inference budget, real billing or cross
data-center cluster consistency. In-flight revocation and model-retag
time-of-check/time-of-use risks remain. A compromised localhost gateway
with the internal service secret can cause budget depletion (bounded by
the configured maximum); operators must protect that credential.

A true billable usage meter will require independently trusted,
runtime-attested model token counts, discrepancy resolution and durable
auditable usage records; **do not treat these conservative reservations
as a billable token meter**.
