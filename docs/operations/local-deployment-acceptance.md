# P1 local deployment lifecycle acceptance (operator-run)

**Status:** runnable acceptance tool with synthetic CI tests only. Real
Ollama and browser acceptance are separate and cannot be claimed by GitHub CI.

The operator already reported successful live Ollama + Go JSON/SSE smoke,
and a Safari screenshot with `assistant → llama3.2:1b` delivering an
inference result. The remaining lifecycle test is Stop → denial → Start
→ chat → Promote → chat → Rollback → chat, using actual local HTTP.

## Safety requirements

This procedure intentionally **changes the local `assistant` routing
state**. Run only on your own idle Mac when no other client is using the
alias. Do not run on shared/production systems. It never starts/stops
Ollama or Go, pulls models, creates accounts, changes global model
approval, or unloads models. It is disabled by default unless the operator
passes `--execute`. The `--candidate` model must already be registered,
approved and loaded; it must be **different** from the baseline.

The script refuses to replace an existing rollback history, rejects an
unready baseline/candidate before any write, checks the exact active digest,
and attempts a guarded restoration if a later request fails. This recovery
is best-effort: model eviction or a concurrent operator action may require
**manual recovery**. Inspect `04 / Model Routing` if the command fails.

Never paste or upload the local operator token. The tool reads the
existing private `reports/local/.model-operator-token` with owner-only
permissions, and uses only fixed `127.0.0.1:8000` API endpoints.
No browser cookies, raw prompts/completions, request identifiers,
model-output text or secret values are written to the result.

## Step 1: read-only preflight (no mutations)

On your Mac, check the operator Model Manager and ensure the existing
`assistant` deployment is **enabled** and routes to `llama3.2:1b`.

~~~sh
cd ~/metriqen-llm-serving-optimization
git pull --ff-only origin main
python3 -m tools.demo.local_deployment_acceptance \
  --name assistant --baseline llama3.2:1b
~~~

Preflight only requires the baseline to be approved and loaded. To
preflight **all** lifecycle steps, first register, approve and load an
additional small model in the Model Manager, and execute:

~~~sh
python3 -m tools.demo.local_deployment_acceptance \
  --name assistant --baseline llama3.2:1b \
  --candidate llama3.2:latest
~~~

This does not write to SQLite or send a model inference request.
It verifies the current alias, baseline/candidate approval and Ollama
loaded readiness using the local FastAPI status APIs. `llama3.2:latest`
is an **example**: choose a distinct revision that is installed and
fits memory. If your Mac cannot hold both, do not force multiple 7B
models into memory or proceed with Promote/Rollback.

## Step 2: explicit local lifecycle (mutates alias)

After verifying both models remain loaded and the alias is idle:

~~~sh
python3 -m tools.demo.local_deployment_acceptance \
  --name assistant --baseline llama3.2:1b \
  --candidate llama3.2:latest --execute \
  --output "reports/local/deployment-lifecycle-$(date +%Y%m%d-%H%M%S).json"
~~~

It performs the following assertions:

1. Checks `assistant` is enabled, ready and pinned to the baseline
   exact registered SHA-256 digest; candidate has its own approved,
   loaded pinned digest.
2. Calls Stop; checks alias disabled; verifies POST chat receives 409.
3. Calls Start; checks enabled/ready and executes real gateway inference
   through the alias, requiring a nonempty model reply.
4. Promotes to candidate; checks active/previous digests and executes a
   real chat using the candidate.
5. Rolls back; checks baseline restored, enabled and ready, then
   executes one more real chat via the stable alias.

On success the script creates a private owner-only JSON under
`reports/local/`, never overwrites it, and prints a short result. The
report contains only model identifiers/digests, assertions, timestamp
and explicit verification limitations. A failure never counts as PASS;
re-check the alias in Safari before new client traffic.

Without a second model, `--execute` **without** `--candidate` performs
only Stop / blocked chat / Start / real chat. That is useful for local
recovery but **does not** verify Promote/Rollback or satisfy full P1.

## Step 3: browser acceptance and final P1 claim

Still use Safari/Chrome to demonstrate the real model-management UX,
deployment state transitions and final rollback. Confirm the token is
not visible in screenshots. The tool cannot verify keyboard/mobile
visuals, SSE streaming (separately verified by `real_ollama`),
GPU/Metal execution, runtime slot count, TTFT/RPS/p95 or tenant guard
configuration. Keep raw reports private and publish only
human-reviewed sanitized evidence.

See [portfolio P1–P6 acceptance](../PORTFOLIO_V1_ACCEPTANCE.md) and
[final Mac handoff](portfolio-final-mac-handoff.md). All release boxes
stay unchecked until independently reviewed operator evidence exists.
