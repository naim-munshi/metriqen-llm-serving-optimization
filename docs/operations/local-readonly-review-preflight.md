# Local P4/P5 service preflight — read-only, no model load

This command checks **already running** loopback services on the operator
Mac. It never launches servers, logs in, reads tokens, changes databases,
requests model generation or needs AC Power. It does **not** approve any of
the [P1–P6 portfolio gates](../PORTFOLIO_V1_ACCEPTANCE.md).

## Dry-run (default, zero network)

```bash
cd ~/metriqen-llm-serving-optimization
source .venv/bin/activate
python3 -m tools.demo.local_readonly_review
```

The default only describes the fixed plan; no service is queried and
nothing is saved.

## Run eight bounded, credential-free local GET checks

With the Go gateway, FastAPI and Ollama **already** running, execute:

```bash
python3 -m tools.demo.local_readonly_review --execute
```

The checks, from exactly three fixed local origins
`127.0.0.1:11434`, `127.0.0.1:8081` and `127.0.0.1:8000`, are:

| Check | Expected | Meaning |
| --- | --- | --- |
| Ollama `/api/version` | HTTP 200 + valid version JSON | Runtime endpoint reachable; no model execution |
| Go `/health/live` | HTTP 200 + expected service/status | Gateway liveness only |
| Go `/health/upstream` | HTTP 200 + `reachable` | Gateway reports Ollama API version reachable; **not model ready** |
| Go `/console` | HTTP 200 + Metriqen HTML | Basic static UI HTTP surface, not browser rendering |
| FastAPI `/health/live` | HTTP 200 + expected service/status | Control API liveness only |
| FastAPI `/console/models` | HTTP 200 + Metriqen HTML | Static Model Manager HTTP surface |
| FastAPI `/api/v1/local/models` | **HTTP 401** without credentials | Model registry denies an unauthenticated anonymous request |
| FastAPI `/console/account` | Optional HTTP 200 | Account *page*, **not** proof of Google login; 404 is recorded as optional unavailable |

All checks use GET. The tool never requests `/auth/login`, never
follows redirects, never reads unsuccessful HTTP error bodies, never
forwards environment proxy settings or cookies, and never sends any
Authorization header. Response reads are capped at 32 KiB. It only
prints static check IDs and HTTP statuses, never bodies, model tags,
account data or secrets. An unreachable or unsafe *required* check
produces exit code `2`. An optional account page failure is not treated
as a public authentication failure because OIDC is intentionally opt-in.

## Optional private sanitized evidence

```bash
umask 077
mkdir -p reports/local
chmod 700 reports/local
python3 -m tools.demo.local_readonly_review --execute \
  --output "reports/local/reviewer-preflight-$(date +%Y%m%d-%H%M%S).json"
```

Only a fresh owner-private `0600` JSON file immediately inside
`reports/local/` is accepted; no path traversal, symlinks or overwrites.
The file contains observed statuses and **explicit false flags** for
real inference, OIDC sign-in, real Safari visual acceptance, public
listener binding and P4/P5 approval. It does not contain response
bodies, model outputs, tokens or identities. Check `git status` before
committing; don't add anything from `reports/local/` to public GitHub.

## What a successful preflight does NOT verify

1. Source `git pull` does not restart existing Go/FastAPI processes.
   The result does not attest which compiled binary/source SHA serves
   these endpoints.
2. A loopback request succeeding does **not** prove the listener binds
   exclusively to 127.0.0.1. On the Mac inspect the process bound ports,
   e.g. `lsof -nP -iTCP:8081 -sTCP:LISTEN` and
   `lsof -nP -iTCP:8000 -sTCP:LISTEN`, and retain private evidence.
3. A Metriqen HTML page is not proof of actual keyboard focus,
   VoiceOver, mobile rendering, usability or safe handling of a
   real user login. Follow [actual reviewer journey](portfolio-local-reviewer-journey.md).
4. An unauthenticated 401 is a narrow security boundary smoke test,
   **not** a penetration test, proof of tenant isolation or correctly
   configured Redis/PostgreSQL/OIDC.
5. No real Ollama generation, model digest, rollback, continuous power,
   TTFT, latency, throughput or model accuracy is measured here. See
   [Mac final handoff](portfolio-final-mac-handoff.md).

The tool's offline unit tests are synthetic. The real `--execute`
report is an **operator-observed service snapshot only**, never a
production-security acceptance certificate.
