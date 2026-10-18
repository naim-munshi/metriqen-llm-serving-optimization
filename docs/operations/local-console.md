# Local Operations Console

Metriqen includes a **read-only engineering dashboard** served from
`http://127.0.0.1:8081/console` by the existing Go inference gateway.
It is a development aid and portfolio demonstration, **not** the secure,
multi-tenant web console planned for the production platform.

## Start on your Mac

Ensure Ollama is running normally, without starting a duplicate process.
From the repository root:

~~~sh
cd services/inference-gateway
METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 \
METRIQEN_ALLOWED_MODELS=llama3.2:1b \
METRIQEN_QUEUE_DEPTH=2 \
go run ./cmd/server
~~~

Open **http://127.0.0.1:8081/console** in a browser. The console needs no
Node, npm, CDN, JavaScript framework, login, or model download beyond
whatever Ollama model the operator chooses to use separately. The
dashboard itself does not run a completion request and does not allocate
gateway admission capacity.

While the page is visible, it refreshes the existing same-origin
read-only endpoints every **five seconds**, and has a manual refresh
button. Network requests use a three-second browser-side timeout and
turn stale/unavailable metrics into visible diagnostic states.
The page displays only:

- Process readiness from `/health/ready`;
- on-demand Ollama API version endpoint reachability via
  `/health/upstream` (not inference/model readiness);
- bounded active/waiting capacity and rejection/queue counters from
  `/metrics/admission`;
- admitted attempt outcomes, including standalone SSE terminal marker
  versus missing marker, plus a mean admitted-handler duration across all
  completed outcomes excluding queue wait, from `/metrics/inference`;
- fixed upstream failure reason counters from `/metrics/upstream`;
- bounded, untrusted provider-reported prompt/output/total token aggregates,
  valid nonstream and stream provider claims, and unmeasured streams
  from `/metrics/usage` (schema v2);
- an **operator-guided, read-only reviewer journey** linking the separate
  Model Manager and optional OIDC Account Console on loopback port 8000.
  Navigation carries no credentials or cookies across origins.

The three sets of counters have **different denominators and meanings**.
Process-local counters reset on gateway restart. A clean upstream SSE
terminal marker is not verification of model answer correctness or
full JSON event validity, and green health status is not a success-rate
or uptime guarantee. No GPU utilization, TTFT, p95, throughput, model
memory pressure, costs, or financial metrics are implied. Reported
token aggregates are provider-supplied, *not independently verified
tokenization or billable usage*. A missing or inconsistent usage report
does not imply zero actual generated tokens. The displayed
handler duration is not TTFT or GPU execution time and does not establish
model performance; it includes failed and canceled admitted requests.

## Reviewer navigation

The operations page now includes a responsive **Explore the local
platform** section linking to exact, fixed local addresses:
`http://127.0.0.1:8000/console/models` (operator token required)
and `http://127.0.0.1:8000/console/account` (separate opt-in OIDC
provider required). Both links open new tabs and send no tokens in
URLs, JavaScript calls or query parameters. They may be unavailable
when optional control API features are disabled. The actual three-console
tour, security boundary and operator screenshot requirements are in
[Portfolio local reviewer journey](portfolio-local-reviewer-journey.md).

## Security boundary and privacy

The application embeds its own fixed HTML/CSS/JS files into the Go
binary. Asset routes `/console`, `/console/app.css`, and
`/console/app.js` are exposed **only when a local inference upstream is
configured**. Health-only Compose deployments do **not** register the
dashboard routes.

- The gateway enforces loopback listeners for configured inference.
  **Never** reverse-proxy or publish this unauthenticated UI or its
  inference/metrics endpoints to external networks.
- No prompts, generated text, raw upstream errors, upstream URLs, user
  identities or model tags are present in dashboard metric values. It
  uses `textContent` for numeric/status updates, not untrusted HTML
  rendering.
- HTTP responses use a restrictive **Content-Security-Policy** for
  same-origin assets and same-origin fetches, plus `Cache-Control:
  no-store`, `nosniff`, `X-Frame-Options: DENY`, and
  `Referrer-Policy: no-referrer`.
- No third-party scripts, frameworks, analytics or external image
  resources are loaded by the dashboard.
- It is entirely **read-only**. No request retries, auto-starting Ollama,
  adaptive scheduling, remote admin operations or background monitoring
  persist after the browser is closed.

The optional shared-secret bearer token applies only to chat POST,
not this unauthenticated read-only console or its metrics endpoints.
See [local bearer-token boundaries](local-bearer-token.md).

This is not a production identity/authorization boundary. Production
UI would require actual gateway authentication, project-isolated
permissions, trusted deployment, quotas, CSRF considerations and safe
session handling.

## Verifying offline without Ollama

From the repository root:

~~~sh
python3 -m tools.demo.offline
~~~

This builds the *real Go gateway* with a synthetic, loopback HTTP upstream
and checks **18 deterministic software contracts**, now including
dashboard asset serving and security headers. It **does not validate
visual layout in a browser** or real model inference. See the
[offline portfolio runbook](offline-portfolio-demo.md) and
[engineering briefing](../PORTFOLIO.md).

To verify dashboard routes directly, Go regression tests cover bundled
asset responses, read-only GET-only methods, CSP/security headers,
unavailable routes when upstream is disabled and zero inference
admission impact. The new reviewer journey tests also check static
loopback-only navigation links, schema-v2 provider metrics,
numeric data rendering via `textContent` and lack of cross-origin
fetch or shared browser secrets. CI still has no browser screenshot or external UI test.

If the console shows `Unavailable`, verify
`GET /health/ready`, `GET /health/upstream`, and the metrics endpoints
directly using the existing operations runbooks. Browser fetch failure
does not by itself prove Ollama crashed.
