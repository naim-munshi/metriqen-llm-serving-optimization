# Local Model Registry and Ollama Preload (Development)

This milestone adds an **opt-in, operator-only local management slice** to the
existing FastAPI control API. It is a functional workflow, not a simulation:
the service queries **installed models from the running Ollama API**,
persistently records their exact digests in a local SQLite database, requires
explicit approval, and requests a native Ollama preload. It then verifies
the loaded model against `GET /api/ps`.

**Boundaries:** This is **not** a deployment scheduler, durable model serving,
GPU optimization, artifact download, revision locking at the Ollama runtime,
web login, or production/multi-tenant model registry. Approval applies only
to the local control API; the separate Go inference gateway does **not**
enforce registered-model approval yet. Do not expose either service publicly.

## Browser-based local model manager

With both opt-in variables configured and the FastAPI control API running
on 127.0.0.1:8000, open **http://127.0.0.1:8000/console/models**.

The responsive, framework-free model manager uses the same authenticated
localhost API for **discovery, registration, approval, preload, unload, **single-turn inference**, status
and refresh**. Paste the local operator token once after the page opens;
the JavaScript keeps it **in memory only** and clears the password input.
Reloading or disconnecting forgets the token. It uses no cookies, localStorage,
CDN, external assets or analytics. All model text is inserted with
textContent, not HTML injection, and the HTML/JS/CSS endpoints ship with a
restrictive Content Security Policy and no-store caching.

The page itself contains no secrets and is available only when the optional
local model management feature is enabled. API calls still require the
loopback client and operator bearer token. This is not a public dashboard,
an authenticated production web console, or an integrated gateway identity
provider. CI validates static assets/CSP; a **real browser visual and
accessibility QA pass** remains required.

## Approved single-turn inference playground

The browser now includes a small prompt editor for each approved registry
entry. Once its exact revision is loaded, it calls
`POST /api/v1/local/models/{tag}/chat` with
`{"prompt":"...","max_tokens":128}`. The control API verifies the
registry approval, installed digest and loaded digest **before** forwarding
a non-streaming chat request to the fixed local Go gateway at
`http://127.0.0.1:8081/v1/chat/completions`. The response is displayed
in the browser via safe text rendering, without persistence in the
database, benchmark history or browser storage.

Limits: 4096 prompt characters and 1–256 requested output tokens, one user
message, temperature 0, nonstreaming JSON, bounded upstream response, no
external origin/redirect/proxy, sanitized upstream errors. A request will
fail if the gateway is down, the registered revision is not loaded, or
the Go gateway has separately enabled an incompatible bearer-token
requirement. Never reuse the control-plane operator token as a Go
gateway token. This path provides local operator approval enforcement,
**not** an authorization boundary for the separate directly reachable
Go gateway. Streaming, multi-turn sessions, per-project usage, deployed
model routing and production identity remain future work.

## Supported workflow

- `GET /api/v1/local/available-models`: discover installed local Ollama tags
  and SHA-256 digests; no remote model catalogue or pulls.
- `POST /api/v1/local/models`: register one installed canonical `name:tag`;
  the server obtains its digest from Ollama and stores it unapproved.
- `GET /api/v1/local/models`: list persisted records.
- `POST /api/v1/local/models/{tag}/approve`: approve a registered revision
  only when the installed digest still matches.
- `POST /api/v1/local/models/{tag}/load`: request a five-minute Ollama preload
  of an approved revision and check the returned loaded-model digest.
- `POST /api/v1/local/models/{tag}/unload`: unload an approved, exact-digest-matched model from Ollama memory; idempotent when already unloaded.
- `GET /api/v1/local/models/{tag}/status`: inspect whether the exact
  registered model revision is reported loaded by Ollama.

Initial canonical model tags are deliberately narrow, e.g. `llama3.2:1b`.
Namespaced model tags and other runtime providers are not yet supported.
Registration refuses duplicate tags rather than silently updating revisions.
A changed model digest requires an explicit future revision-management flow.
No user prompt or model-generated text is stored.

## Security and persistence

Routes are **absent by default (404)**. They are enabled only when both
`METRIQEN_LOCAL_MODEL_TOKEN` and `METRIQEN_LOCAL_MODEL_DB` are set.
Token length: 32–256 printable non-space ASCII characters. Calls require a
loopback client address and exact `Authorization: Bearer <token>`; bad
credentials are rejected before runtime or registry access. This is a single
local operator secret, **not** OIDC, production authentication or RBAC.

Database path must be absolute and end with `.sqlite3`. The operator-enabled
service creates the local database/table and sets the file mode to 0600 on
POSIX. Keep it under gitignored `reports/local/`. Do not use this local SQLite
registry as the multi-tenant PostgreSQL membership database. The only
permitted Ollama origin is `http://127.0.0.1:11434`; outbound proxies and
redirects are disabled and responses are size-bounded.

Never bind control API outside 127.0.0.1, reverse-proxy these endpoints,
publish the token, or enable them on a public host.

## Local setup (Mac)

Use **Terminal 1** for your existing single Ollama instance. If
`Ollama.app` already owns port 11434, keep it running; do **not** start a
second `ollama serve`. Verify that `llama3.2:1b` is already installed.

Use **Terminal 2** for the Go gateway, if testing chat inference:
`METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 go run ./cmd/server`
from `services/inference-gateway`; the gateway must stay loopback-only.

Use **Terminal 4** for the opt-in control API:

~~~bash
cd ~/metriqen-llm-serving-optimization
mkdir -p reports/local
umask 077
python3 - <<'PY'
from pathlib import Path
import secrets
path = Path("reports/local/.model-operator-token")
if path.exists():
    raise SystemExit("Token already exists; retain it, do not rotate silently")
path.write_text(secrets.token_urlsafe(40), encoding="ascii")
path.chmod(0o600)
PY
export METRIQEN_LOCAL_MODEL_TOKEN="$(cat reports/local/.model-operator-token)"
export METRIQEN_LOCAL_MODEL_DB="$PWD/reports/local/models.sqlite3"
cd services/control-api
# Activate your existing environment with FastAPI/uvicorn installed.
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

Do not print the token to your terminal or paste it into chat.
For **Terminal 3** local API calls:

~~~bash
cd ~/metriqen-llm-serving-optimization
TOKEN="$(cat reports/local/.model-operator-token)"
API=http://127.0.0.1:8000/api/v1/local
curl -fsS "$API/available-models" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
curl -fsS "$API/models" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

curl -fsS -X POST "$API/models"   -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json"   -d '{"tag":"llama3.2:1b"}' | python3 -m json.tool
curl -fsS -X POST "$API/models/llama3.2:1b/approve"   -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
curl -fsS -X POST "$API/models/llama3.2:1b/load"   -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
curl -fsS "$API/models/llama3.2:1b/status"   -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
~~~

The preload may use significant memory and may block up to 120 seconds on
the Mac. It is **not** an inference benchmark. If the model is missing,
`POST /models` returns 404 rather than silently pulling it. If a tag
has changed to different weights, approval/load returns 409 rather than
silently updating the approved digest. Status is a point-in-time snapshot,
not an uptime guarantee.

You can separately test an existing approved/loaded model through the Go
gateway's `/v1/chat/completions` route, but **the gateway currently accepts
its own model-tag allowlist independently**. Registration does not yet
authorize inference. Integrating the control-plane model registry with the
gateway, deployment lifecycle, quotas and secure session management remains
a separate milestone.

## Verification and release boundary

`python -m pytest -q tests/test_local_models.py` from
`services/control-api` runs synthetic, offline registration, security,
persistence, revision-mismatch and HTTP adapter tests. CI does **not**
run a real Ollama model, load GPU weights, attest model revisions, or
prove the functioning of the native API on your Mac. A real-Ollama
operator acceptance test is required before marking live preload verified.
