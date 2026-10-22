# Metriqen public platform: non-negotiable deployment boundaries

**Status: BLOCKED for public production ingress.** This is a documented
infrastructure/security contract, **not** a deployable production Compose,
Kubernetes or GPU setup. It intentionally does not require the owner to
choose a Google OAuth support email yet. Do not place the development
API, Go/Ollama inference listener, localhost operator token, or SQLite
registry behind an internet-facing reverse proxy.

## Separation of concerns

```text
Public HTTPS Browser / Google OAuth
             |
     TLS edge / exact host
             |
  HTTPS-only Production BFF (FUTURE; not the local FastAPI)
             |   authenticated Redis TLS + PostgreSQL TLS
             |
  Project-scoped authenticated inference API
             |
  Private provider-side worker proxy / service auth
             |
  Private pinned-model GPU worker pool (FUTURE)
```

The existing 127.0.0.1:8000 and 127.0.0.1:8081 instances remain
**development components** even if they are running successfully on the
Mac. The local operator Model Manager API has its own shared secret and
is **not** a multi-user production administration surface.

## Required before switching on any internet listener

| Boundary | Release-proof requirement | Current status |
| --- | --- | --- |
| Domain and TLS | Owner-controlled domain, HTTPS certificate, exact server Host, strict trusted proxy chains, HSTS | Not configured |
| Google login | Exact HTTPS redirect, real consent/client, key rotation, nonce and CSRF negative tests, real Safari/Chrome login | Synthetic tests only |
| Browser session | Separate production-only BFF, `__Host-` Secure HttpOnly cookie, SameSite Lax, origin-bound CSRF, revocation, MFA policy | Local-only Secure=False cookie |
| Tenant boundaries | Independent 2-user A/B org/project isolation tests with PostgreSQL-authoritative permissions, no JWT role trust | Local code/CI only |
| Inference | Public endpoints authenticated, project-scoped, strict model digest/deployment checks; private GPU access | Default Go gateway is local/unguarded |
| Quotas | Shared Redis quota + in-flight and output-cap reservations consistently on **all** gateway replicas | Optional local mechanisms |
| Data | Managed TLS PostgreSQL/Redis, schema migrations, health/readiness, encrypted secrets and restored backup tests | Not hosted |
| GPU and cost | Provider-owned GPU account, hard spending cap, private worker ingress, measured queue state, warmup/draining | Offline scaling policy only |
| Operations | Audit, dashboards/alerts, rate-abuse handling, dependency scans, incident recovery and operator rollback | No production acceptance |

**A passed CI suite is not a license to publish.** Each deployed feature
needs real independent acceptance evidence and owner approval. The new
`tools/autoscaling/policy.py` determines candidate worker counts, but
`executed=False` guarantees there is no GPU scale action.

## Deployment controls

1. Keep `compose.yaml` strictly localhost published: the existing
   health-only container gateway can bind 0.0.0.0 *inside* its container
   but Docker host publishes only `127.0.0.1`. Never remove that
   boundary without a separate reviewed production configuration.
2. Preserve `secureGatewayHandler` Host-header checks,
   `listenerAllowed` loopback enforcement in inference mode, and
   FastAPI's `enforce_local_http_host=True` real-ASGI entrypoint.
3. Do **not** reuse `scripts/start-local-demo.sh` in a cloud deployment.
   Its browser pages, token, test model and HTTP cookies are dev-only.
4. Do not use local Google provider compatibility as complete public
   OAuth. It accepts a signed Google **ID token** only into a private
   browser session; `/api/v1` still requires a dedicated `at+jwt`.
5. Put provider-authenticated GPU workers behind a service identity
   with explicit tenant authorization. Never forward caller-supplied
   service URLs, raw JWT roles or the operator SQLite token to Ollama.
6. Explicitly approve GPU costs; never equate a planning policy's
   hourly estimate with a GPU provider hard budget.

## Security regression checks

The existing synthetic Go/FastAPI security tests and a new offline
source-level guard `tests/e2e/test_public_exposure_guard.py` run as part
of the GitHub CI e2e job. They check that the existing developer setup
does not silently become host-public, and that documentation never
confuses those checks with public security attestation.

For the first actual public system, build a **new independently hardened
production entrypoint** and test the real TLS/identity/Redis/PG/GPU
wiring before deployment. Do not make current development routes
public by flipping `host=0.0.0.0` or proxying port 8000/8081.

Related acceptance:
[#114 HTTPS and tenant boundary](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/114),
[#115 real Google OAuth](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/115),
[#116 GPU autoscaling](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/116).
