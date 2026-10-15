# Portfolio demo — real Go gateway, deterministic synthetic upstream

A reviewer can exercise Metriqen's **actual Go inference gateway** without
installing Ollama, downloading a model, setting API keys, or paying for GPU
capacity. This is a **contract demonstration**, not a model inference run,
serving benchmark, optimization comparison, or GPU measurement.

## One-command execution

Requires **Go 1.23+** and **Python 3.12+**. From the repository root:

~~~sh
python3 -m tools.demo.offline
~~~

The demo uses only the Python standard library and existing Go sources. It
builds the Go executable in a private temporary directory, starts a local
synthetic HTTP model fixture and a separate temporary **loopback-only**
Metriqen gateway, checks the system, prints one JSON summary, then
terminates both services and removes the temporary binary.

**It does not touch the operator's existing Ollama server or Gateway** on
ports 11434 and 8081. Both demo ports are selected from free loopback
ports at run time; a rare bind race causes a fail-closed demo error rather
than public exposure.

## What it proves and what it does not

The self-contained demonstration exercises the following contracts using
the real Go gateway server:

| Check | Expected property |
| --- | --- |
| Gateway process readiness | Local gateway health route responds |
| Upstream API reachability | Synthetic fixture's Ollama-compatible version API responds |
| Non-stream chat completion | Gateway forwards and validates bounded JSON |
| Streaming chat | SSE data and a terminal \`[DONE]\` marker arrive |
| Truncated streaming | Gateway v2 metrics distinguish clean EOF without a standalone terminal `[DONE]` marker |
| Model allowlist | Disallowed tag returns HTTP 403 and does not reach fixture |
| Upstream HTTP 429 | Sanitized rejection forwarded without exposing upstream error body |
| Malformed upstream JSON | Gateway returns sanitized HTTP 502 instead of false success |
| Failure metrics | Rejected/status and malformed response counters reconcile |
| Bounded queue saturation | Four held active requests, two queued, seventh HTTP 429 |
| No queued forwarding | Fixture observes exactly four held requests before release |
| Queue handoff | Two waiting requests complete after the four active slots release |
| Admission counters | Active/waiting return to zero; 2 queued, 1 full rejection |
| Privacy | Synthetic private prompt marker is not present in exported metrics |
| Local console assets | Gateway serves read-only embedded UI/JavaScript with no-store and restrictive CSP headers |
| Optional local bearer gate | A second temporary, real Go gateway rejects missing/wrong-token POSTs (401) and accepts one synthetic-token completion without leaking the credential |
| Post-admission duration accounting | Gateway schema v3 returns a fixed nine-bin histogram for 11 completed attempts, without private attributes. Not a model latency benchmark |
| Inference outcome accounting | Eleven admitted fixture requests partition into seven validated non-stream writes, two clean-stream EOFs (one standalone DONE event, one truncated) and two pre-response failures; the admission 429 is excluded |

The mock's blocking behavior is controlled by an explicit synchronization
event, not by slow model generation or an approximate sleep duration.
Short polling of real gateway gauges is used only to establish admission
states before queueing the next request. The server-side Go FIFO ordering,
cancellation and timeout tests remain separate, stricter unit/integration
proofs.

The output contains:

~~~json
{
  "demo_kind": "synthetic_gateway_contract_demo",
  "model_inference_executed": false,
  "performance_measurement": false,
  "passed": true,
  "checks_passed": 18
}
~~~

This **illustrative subset** is not an execution result. The actual CLI
prints additional fixed, aggregate counters and the 18 passed check names.
A failed assertion exits nonzero without printing private fixture content.
The output should not be reported as LLM accuracy, runtime throughput,
token latency or GPU performance.

## CI verification

Existing GitHub Actions \`local-inference-e2e\` runs
\`python -m unittest discover -s tests/e2e -v\`, including
\`tests/e2e/test_offline_portfolio_demo.py\`. The test invokes the
**same one-command CLI** and verifies both successful reconciled counters
and fail-closed behavior when Go is unavailable. The CI model is synthetic.
Go race-enabled tests are run in the separate \`gateway-tests\` job.

For actual Apple Silicon + Ollama evidence, follow the
[controlled benchmark protocol](../benchmarks/controlled-comparison.md)
and clearly label operator-reported hardware/model measurements separately.

## Security and portability

The demo never opens network-facing listeners, follows external redirects
or uses environment proxies for HTTP probes. It does not print the raw
synthetic fixture prompt, response, or upstream error body. No external
network access, API key, or persistent service is needed.

This **does not** add authentication, full multi-tenancy, vLLM, batching,
cost accounting, or automatic inference recovery. The gateway is still
development-only and must not be exposed outside loopback. Read the
[security policy](../../SECURITY.md) before adapting the code for any
public deployment.
