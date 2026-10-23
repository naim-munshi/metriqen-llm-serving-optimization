# Local portfolio reviewer journey — three existing consoles

**Scope:** a coherent, **operator-guided** Portfolio v1.0 walkthrough
of Metriqen's actual development tools. This does **not** create a shared
browser session, deploy hosted GPU models, unify production security
domains, or make the app internet-accessible. The interface and read-only
metrics are implemented; screenshots and real Mac acceptance **remain
operator-run**.

## Quick route map

| Surface | Exact browser URL | Requirements | Security boundary |
| --- | --- | --- | --- |
| Go gateway operations | `http://127.0.0.1:8081/console` | Locally configured Go gateway with an Ollama upstream | Read-only process metrics; no login; never publish beyond loopback |
| FastAPI model manager | `http://127.0.0.1:8000/console/models` | FastAPI, `METRIQEN_LOCAL_MODEL_TOKEN` and local registry SQLite enabled; installed Ollama model | Separate local operator bearer token in browser memory; **not** a project OIDC login |
| FastAPI account console | `http://127.0.0.1:8000/console/account` | Real, trusted configured OIDC provider, PostgreSQL and Redis; optional | Verified browser session, **not** the operator token or automatically a Go gateway credential |

All links are static loopback browser navigations. They open in a new
tab and carry **no JWT, password, local bearer token, prompt, query
string token or browser credentials across ports**. No JavaScript
calls the other service origin; each console makes only its own
same-origin requests. Port 8000 tools can legitimately show connection
refused / 404 when the corresponding opt-in feature is disabled;
this is not evidence the Go gateway failed.

## Run an honest demo

1. Start the existing Ollama process and the Go gateway bound to
   127.0.0.1:8081. Verify `/health/live` and `/health/upstream`
   independently. See [Go local gateway setup](local-inference.md).
2. Open the **Go operations console**. The overview shows process
   state, admission, outcomes, error classifications and process-local
   **provider-reported** input/output token counters. The new
   **Explore the local platform** panel links to other tools without
   sending credentials or asking for network permissions.
3. Open the optional local **Model Manager**, connect with its
   separately configured operator token, register an installed pinned
   Ollama tag/digest, approve, load and use the nonstream prompt
   playground. Record a **real** screenshot only after confirming the
   UI and local runtime on the operator's machine. See
   [local model registry](local-model-registry.md).
4. Return to Go operations (still on port 8081) and refresh the
   read-only counters. If usage is missing or inconsistent, the
   dashboard displays unavailable or unmeasured values. A provider
   reported token sum is **not** an independently calculated token
   count, exact cost, billed usage or throughput/p95 latency.
5. With the same authorized local operator token, use the Model
   Manager's named deployment controls to promote an approved
   digest, stop/start and roll back. **The local operator workflow
   does not by itself enforce this approval on a direct unguarded
   Go gateway call**; project-specific SQL authorization and
   digest-bound routing are separately opt-in guarded features.
6. **Only if actually configured:** open the Account Console,
   exercise trusted OIDC login, create a verified profile and
   inspect organizations/projects. Note that an OIDC identity here
   does not auto-authorize operator model management or bridge
   the separate gateway without explicit configuration.
7. After the UI walkthrough, run
   [the real Ollama acceptance command](real-ollama-portfolio-acceptance.md)
   on the operator Mac, and preserve the private report. Then perform
   [prospective A/B/A/B evidence validation](../benchmarks/phase-preflight-binding.md).

## Keyboard and narrow-screen reviewer checks

The Go Operations console now retains all six navigable section links at
760–880px tablet widths and provides a horizontal, scrollable navigation row
at 600px and below, instead of hiding most/all links. Its first keyboard
focus is a visible-on-focus **Skip to main operations content** link, with
an explicit `#overview` target. Status labels and long health text can
wrap on a narrow phone. Static Node tests guard these DOM and CSS contracts,
**not** actual Safari rendering, focus visibility or mobile touch comfort.

**Operator acceptance still needed:** With real Mac data in Safari and/or
Chrome, check desktop plus approximately 390px and 320px viewport widths,
scroll the horizontal section nav to reach **Provider token usage** and
**Reviewer workflow**, use Tab/Shift+Tab/Enter and the skip link without
mouse interaction, confirm no unintended document-wide horizontal overflow,
and inspect strong focus rings and legible health status. Capture only
authentic, approved screenshots. Missing Mac/browser evidence keeps P4 open.

### Polling control and screen-reader review (source implementation)

The Go Operations console now has a focusable **Pause auto** button
beside the explicit **Refresh** control. `aria-pressed=true` denotes
paused automatic polling; its label changes to **Resume auto**. Paused
mode performs **no new timer-triggered or tab-visibility-triggered**
metrics requests, while explicit Refresh still works. State lives only
in the current tab's memory, with no local storage or cookies.
A refresh already in progress may finish once after Pause is clicked.

The live gateway status text is only replaced when its value changes,
avoiding repetitive identical screen-reader announcements on each
five-second refresh. It still announces health changes or recovery.

**Manual checks remaining:** At 390px and 320px Safari widths, Tab from
the skip link into the dashboard, confirm both Pause auto and Refresh
are visible and have at least 44px tap targets. Activate Pause by
keyboard and confirm automatic network polling stops. Verify manual
Refresh still samples health; Resume restarts 5-second polling when
visible, without inference requests. Check VoiceOver announces actual
health changes, not identical statuses every five seconds. Node tests
validate the behavior with a mocked DOM, **not** Safari VoiceOver.

## Control-plane keyboard parity

The Model Manager and optional Account Console now expose their own
first-focus **Skip to ... content** links with labelled main targets,
matching the Go Operations console's keyboard shortcut. The Model
Manager explicitly styles keyboard focus on native select/input fields
and honors `prefers-reduced-motion` when scrolling. On small screens,
the Account Console's three existing navigation links stay accessible in
a horizontal scroll area with 44px-minimum touch targets. No sessions,
credentials, or cross-origin behavior are changed.

These static HTML/CSS contracts have Node tests but **are not evidence of
actual Safari/Chrome rendering**. The operator must still test each
real, configured surface with Tab/Shift+Tab/Enter and at 320–390px widths;
optional OIDC login may legitimately be unavailable without a real IdP,
PostgreSQL and Redis. Only release human-reviewed sanitized screenshots.

### Account-console sign-out privacy guard

After the existing same-origin `/auth/logout` succeeds, the optional
Account Console now explicitly removes the previous verified subject,
issuer, profile draft, organization draft, pending invitations, workspace
DOM cards and status notes, rather than merely setting the member panel
to `hidden`. The browser's ephemeral CSRF value and profile-existence
flag are cleared too. Failed logout attempts **do not** claim that the
server session was revoked or silently discard it.

This is a **local browser DOM privacy improvement**, not proof of
server-side session revocation across other devices, a production
security audit, or full cancellation of every outstanding request.
A mocked-DOM Node test verifies fields disappear following an accepted
logout; real Google provider/Redis/PostgreSQL browser acceptance is
still mandatory before production claims. No credentials are persisted.

## Screenshot/video checklist

Capture the **actual rendered Mac browser**, not synthetic CI HTML, after
a human checks layout and mobile width:

- Gateway console: health, inference outcome and reported token panels
  with explicit "untrusted self-report" labeling.
- Model manager: real installed revision, approved status, loaded model
  and nonstream completion (blur the actual generated prompt/output
  unless safe for public release).
- Local deployment: recorded promotion and rollback using an approved
  revision. Never fake an image of a feature that has not been exercised.
- Optional OIDC: only show generic identity and workspace screens
  with personally identifiable fields redacted; never show access
  tokens, local operator secrets, cookies, private keys or database DSNs.
- Explain in README what configuration was actually exercised and
  whether the local experiment included auth/tenant security.

No screenshot, video or browser accessibility audit has been generated
by CI. Automated tests assert route availability, headers, document
structure and no-cross-origin browser scripting; these are **not**
proof of visual quality, actual Ollama execution or a production
authentication boundary.

## Telemetry semantics

Go console `GET /metrics/usage` renders only six fixed aggregate
schema-v2 fields, alongside other already existing process metrics:
reported prompt, output and total tokens; plausible complete nonstream
and streaming provider reports; and streams without a plausible report.
If the response schema is incompatible or numeric totals inconsistent,
the view renders unavailable rather than fabricating zero.

Only internally consistent provider **claims** contribute positive
token aggregates; reporting zero for a missing value would be
misleading. These counters reset with the gateway and do not show
individual tenant, user, model, prompt or response data.
The separately configured Redis full-output-cap **reservation** is
not reconciled by these figures. For exact definitions see
[provider usage telemetry](provider-usage-observability.md).

## Portfolio v1.0 release gating

This PR advances **P4** (coherent reviewer workflow). It does **not**
complete P4 until someone runs the UI, checks responsiveness and
accessibility in a real browser, records sanitized authentic visual
evidence and verifies the model manager to gateway workflow. Refer to
[all six v1.0 evidence gates](../PORTFOLIO_V1_ACCEPTANCE.md) and the
[GitHub release tracker](https://github.com/naim-munshi/metriqen-llm-serving-optimization/issues/81).
