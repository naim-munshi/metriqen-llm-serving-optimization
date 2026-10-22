# Start the local Metriqen reviewer demo on macOS

This is a **local operator convenience launcher**, not a remotely managed
application, hosted demo, background agent, production deployment, or
P1–P6 release acceptance. It only handles existing local development
services. It never exposes a public listener or fetches model weights.

## Prerequisites

- From the repository root, have Go, Ollama, curl and a working Mac.
- Install and keep the existing Python virtual environment:
  `source .venv/bin/activate` and
  `python3 -m pip install -e "./services/control-api[dev]"`.
- `ollama list` must include `llama3.2:1b`. If not, explicitly pull it
  over a trusted connection: `ollama pull llama3.2:1b`.
- Keep `reports/local/` private; never copy operator tokens into chat,
  GitHub, screenshots or benchmark artifacts.

## Launch

```bash
cd ~/metriqen-llm-serving-optimization
git pull --ff-only origin main
bash scripts/start-local-demo.sh
```

The launcher performs these checks and operations, stopping on failure:

1. Reuses a healthy local Ollama; otherwise opens Ollama.app or starts one
   `ollama serve` process on the host. Rejects an occupied/unhealthy
   port 11434 rather than starting a duplicate.
2. Validates the existing private operator token or creates exactly one
   mode-0600 random token; **never overwrites or prints it**.
3. Reuses an existing local Go inference console, otherwise builds and starts
   the gateway with a fixed loopback Ollama origin, `llama3.2:1b` allowlist
   and default zero-depth FIFO; rejects an occupied/unhealthy port 8081.
4. Reuses a local Model Manager, otherwise starts opt-in FastAPI with private
   token and SQLite registry on `127.0.0.1:8000`; rejects an occupied port
   whose manager is unavailable. The unauthenticated model API must return
   `401`, not `200` or `404`.
5. Preloads the **already installed** model for ten minutes and sends a bounded
   **real** nonstreaming chat request through the Go gateway to Ollama.
   This is a startup smoke, **not** a measured speedup, formal real-model
   P1 acceptance, or proof of model quality.
6. Opens the two browser consoles (if the current user has a GUI).

```text
http://127.0.0.1:8081/console
http://127.0.0.1:8000/console/models
```

In Model Manager, use the clipboard safely rather than displaying a token in
Terminal:

```bash
cd ~/metriqen-llm-serving-optimization
pbcopy < reports/local/.model-operator-token
```

Paste into the local operator token field. Then explicitly discover, register,
approve and load the installed model, test the single-turn chat playground,
and demonstrate Stop/Start/Promote/Rollback if the named deployment exists.
The launcher **does not** approve a model or change deployments automatically.

The optional `/console/account` interface needs a separate trusted OIDC
provider, PostgreSQL and Redis. The launcher intentionally does not claim
this separate user-login environment is ready or share operator secrets
with the account console.

## Important limits and troubleshooting

- The default gateway in this quick demo is a **loopback-only, unguarded
  local development** inference route. Never expose or reverse-proxy it.
  For guarded project/lease/redis acceptance, use the specialized operator
  runbooks rather than this launcher.
- If an existing gateway has a bearer or project guard enabled, it may be
  reused but the final smoke can fail; the script **does not disable**
  existing protections or restart an existing process.
- If `127.0.0.1:8000` already runs FastAPI without the Model Manager opt-in
  environment, stop/reconfigure that particular instance manually. The
  launcher **will not kill or reconfigure** a service it did not start.
- Private startup logs and PID hints (only for processes started by the
  script) live in `reports/local/demo-run/` and are Git-ignored. Processes
  remain running after the launch command exits. To stop them, inspect
  `lsof -nP -iTCP:8000 -sTCP:LISTEN` and
  `lsof -nP -iTCP:8081 -sTCP:LISTEN`, verify the owner/process, then send
  SIGTERM to the correct process; don't blindly kill a saved PID.
- If Ollama TLS model-recommendations refresh fails, this does not
  automatically imply local inference is down. The existing model and
  localhost health checks are independent of the external catalogue.
- No real browser rendering/accessibility, live IdP, tenant isolation,
  PostgreSQL/Redis recovery, stable power/thermals or benchmark speedup
  is proven by this launcher. Consult
  [the six final release gates](../PORTFOLIO_V1_ACCEPTANCE.md).

For manual step-by-step control use
[local inference](local-inference.md),
[the local model registry](local-model-registry.md) and
[the three-console reviewer journey](portfolio-local-reviewer-journey.md).
