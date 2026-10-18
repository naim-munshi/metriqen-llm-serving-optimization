# Real local Ollama + Go gateway portfolio acceptance

**Purpose:** an operator-owned, repeatable, low-cost **real-model smoke**
for Portfolio v1.0 gate P1. This is not an A/B/A/B experiment, profiler,
GPU attestation, security audit or commercial deployment. GitHub CI tests
the smoke-check command **with synthetic injected HTTP responses only**.

## Preconditions on the Mac

Use the operator's existing Apple M2 Mac, with exactly the intended local
Ollama server and Metriqen Go gateway running. **Do not run the script
against public, forwarded, tunneled or cloud endpoints.** It contacts only:

- `http://127.0.0.1:11434` (native Ollama installed/loaded model APIs and
  one native chat generation)
- `http://127.0.0.1:8081` (Go gateway readiness, upstream reachability,
  inference metrics, one JSON chat and one SSE chat)

The checker does **not** pull/download models, restart any service, change
Ollama parallelism, preload/unload, create deployment records, bypass JWT
project authorization, or start a public listener. It sends the same fixed
short technical demo prompt three times, requesting at most 32 output
tokens. The runtime will naturally load or keep alive the requested model
while processing the requests; the checker does not promise unchanged
memory occupancy.

From the checked-out repository, verify the actual exact model digest:

```sh
git pull --ff-only origin main
curl -fsS http://127.0.0.1:11434/api/tags | \
  python3 -c 'import json,sys; m=[v for v in json.load(sys.stdin)["models"] if v["name"]=="llama3.2:1b"]; assert len(m)==1; print(m[0]["digest"])'
```

Record the **full 64-character lowercase SHA-256 digest** returned on
your machine; do not copy the example SHA or fabricate it. Verify
`ollama --version` and `curl -fsS
http://127.0.0.1:8081/health/upstream` independently. Ensure the
gateway is already running with `METRIQEN_UPSTREAM_URL` pinned to
Ollama's loopback port; see [local inference](local-inference.md).

## Execute the smoke

```sh
python3 -m tools.demo.real_ollama \
  --model llama3.2:1b \
  --expected-digest FULL_ACTUAL_64_CHARACTER_LOWERCASE_SHA256_DIGEST \
  --output reports/local/real-ollama-smoke.json
```

If the local Go gateway is configured with its **developer-only bearer
secret**, set `METRIQEN_GATEWAY_BEARER_TOKEN` in the client process as
well; the checker sends it only to the two Go chat requests, never to
Ollama, other URLs, output JSON or stdout. Avoid typing/pasting
credentials into screenshots, terminals you record or the repository.

This is strictly a **local developer smoke path**. If the gateway runs
with OIDC + project/deployment authorization, the checker does **not**
disable these policies or generate an identity: that protected mode
requires a separate real IdP+PostgreSQL acceptance flow and will reject
unauthorized smoke requests. A 401/403 is an actionable rejection, not
permission to turn off an intended security policy on a live service.

## Verification contract

The smoke exits **0** and optionally stores a new private JSON only if
all of the following occurred:

1. Native Ollama `/api/version` and `/api/tags` are reachable and
   the exact installed tag's SHA-256 digest matches the operator's pin.
2. Go `/health/live` and `/health/upstream` identify a healthy,
   reachable *local* gateway/upstream API.
3. Ollama `POST /api/chat` returns a completed, nonempty model text
   response for the intended exact tag.
4. Go `POST /v1/chat/completions` returns nonempty nonstream content
   for that same model.
5. Go SSE chat returns at least one nonempty generated text delta,
   one standalone terminal `data: [DONE]`, no post-terminal events,
   and no in-band error. Optional final `choices:[]` usage metadata
   is only accepted in the correct position; its counts are untrusted.
6. Native `/api/ps` shows the exact digest loaded, `/api/tags` still
   matches the pinned installed digest, and the Go gateway's
   `attempts_total`, `nonstream_complete_total`,
   `stream_done_total` counters increased appropriately.

No raw prompt, generated response body, API key, login JWT, model
instructions or local filesystem details are written to the report.
The command refuses to overwrite evidence and permits output **only
below Git-ignored `reports/local/`**, with restrictive file mode. On
failure it returns **2** and emits a sanitized diagnostic, not an
upstream error body.

**Important limitations:** this establishes only **observed local HTTP
model behavior**, not proof of hardware execution, GPU acceleration,
authentic weights, benchmark performance or project-scoped identity.
A fake Ollama-compatible local fixture can also satisfy HTTP contracts;
the operator must verify the actual Ollama process and Metal logs.
Do not report any RPS, TTFT, p95, memory improvement or quality score
from a three-call smoke.

## Next portfolio acceptance steps

After P1 passes, use [A/B/A/B phase preflight binding](../benchmarks/phase-preflight-binding.md)
and [controlled comparison](../benchmarks/controlled-comparison.md)
to collect real, quality-aware A/B/A/B evidence. Then demonstrate
approved model → actual chat → monitoring → version switch/rollback in
the local operator UI, with genuine browser screenshots and a short
video recorded by the operator.

Track all six completion gates in
[Portfolio v1.0 acceptance](../PORTFOLIO_V1_ACCEPTANCE.md). Do not
mark P2–P6 complete just because this smoke succeeded.
