# Optional local inference bearer-token gate

Metriqen's Go inference Gateway is a **loopback-only development service**.
The optional `METRIQEN_GATEWAY_BEARER_TOKEN` environment variable adds a
single operator-managed shared-secret check on
`POST /v1/chat/completions`.

This is **not** multi-tenant authentication, control-plane JWT/RBAC,
account management, token rotation, rate limits, authorization by
project, TLS or permission to expose the Gateway to a public network.

## Enable locally

Generate a high-entropy **new local token** in a terminal using a
cryptographically secure random generator (for example
`openssl rand -hex 32`, which emits 64 printable ASCII characters).
Keep it private: never publish it, commit it, paste it in issues or
include it in a screenshot.

With a locally running Ollama instance and the token assigned to an
operator-controlled environment variable:

~~~sh
cd services/inference-gateway
METRIQEN_UPSTREAM_URL=http://127.0.0.1:11434 \
METRIQEN_ALLOWED_MODELS=llama3.2:1b \
METRIQEN_GATEWAY_BEARER_TOKEN="$LOCAL_SECRET" \
go run ./cmd/server
~~~

`LOCAL_SECRET` should be set by the operator from a protected local
secret source, **not** substituted with a committed/example token.
Set the *same* secret securely for your local test client:

~~~sh
curl -sS --fail-with-body http://127.0.0.1:8081/v1/chat/completions \
  -H "Authorization: Bearer $LOCAL_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3.2:1b","messages":[{"role":"user","content":"Reply ready"}],"max_tokens":16,"stream":false}'
~~~

The environment variable is **optional**. If unset or empty, the old
local-only unauthenticated behavior stays unchanged, so existing
synthetic experiments/benchmarks can remain reproducible. If specified,
it must be **32–256 bytes of visible ASCII** with no whitespace, comma
or quote; invalid configuration fails service startup. A token
configured without an inference upstream also fails startup.

If enabled:

- Missing, malformed, duplicate or incorrect `Authorization`
  values yield HTTP **401** with `authentication_required` and
  `WWW-Authenticate: Bearer realm="metriqen-local"`.
  No secret is echoed in responses.
- The check runs **before** request JSON parsing, model allowlisting,
  admission/queue reservation or upstream forwarding; rejected calls
  never increment inferred-attempt counters.
- The shared secret is compared using Go's
  `crypto/subtle.ConstantTimeCompare`.
- Only the chat **POST** route is gated. The local dashboard, health and
  aggregate metrics remain read-only and **unauthenticated**.
- The Gateway intentionally does **not** forward `Authorization`
  or other caller headers to Ollama, with or without this option.
- Startup logs, aggregate metrics and deterministic tests never
  publish the configured local secret.

## Boundaries

This protects against opportunistic unauthenticated callers reaching
the configured loopback chat route; it cannot isolate different users
sharing the token, prevent malicious code already running as the local
user from acquiring secrets, or replace browser-origin validation,
quotas and server-side tenant-scoped authorization. Environment
variables can be visible to sufficiently privileged local processes;
manage them as secrets.

**Do not bind** the authenticated or unauthenticated inference Gateway
to `0.0.0.0` or otherwise proxy it to untrusted networks. Listener
validation intentionally enforces loopback even when the token is set.

The accepted token is a configuration value, **not** an approved model
digest or permission to change inference behavior. For real-model
comparisons record whether this optional gate is enabled, since
changing the gateway configuration can change request overhead.

## Automated verification

Deterministic Go tests verify strict startup validation, no forwarded
credential, rejected request not reaching the scheduler or upstream,
read-only diagnostic behavior, and unchanged default mode. The
one-command real-Go synthetic CLI exercises a second temporary
loopback Gateway with its own **synthetic test token**, verifies
unauthorized HTTP 401 and one authorized HTTP 200, and checks that
only the authorized request enters inference accounting.

This is a software contract test, not actual identity or security
penetration testing. See [local inference operations](local-inference.md)
and [portfolio briefing](../PORTFOLIO.md).
