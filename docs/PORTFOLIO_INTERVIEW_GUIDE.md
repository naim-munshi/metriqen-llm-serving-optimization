# Metriqen — Engineering interview and live demo guide

**Audience:** junior AI Infrastructure, LLM Platform, ML Systems and backend
engineering interviewers. **Status:** local research prototype, not
released v1.0, commercial SaaS, vLLM serving or deployed GPU autoscaler.

## 30-second truthful project pitch

> Metriqen is my local LLM serving and measurement platform. I built a
> Go OpenAI-compatible inference gateway in front of Ollama, with bounded
> concurrent admission, streaming/cancellation and failure accounting.
> A separate FastAPI control plane manages approved local model revisions,
> deployment rollback and opt-in PostgreSQL/Redis identity and project
> controls. Repeated benchmarks and answer-quality checks examine
> throughput, time to first token, tail latency and overload trade-offs.
> The published evidence includes negative results; I do not claim a
> causal model-serving speedup, public production deployment or real GPU
> autoscaling yet.

## Recruiter demo script (2–3 minutes, operator-controlled)

| Segment | Show on the real Mac | Precise narration |
| --- | --- | --- |
| 00:00–00:20 | GitHub README, architecture and latest green CI | Local-only Go data plane, FastAPI control plane and Python evidence tools; hosted tests do not run real Ollama |
| 00:20–00:50 | Gateway read-only operations console at `127.0.0.1:8081/console` | Admission, outcomes, upstream failures and self-reported token telemetry are process-local counters, **not p95/TTFT or billable usage** |
| 00:50–01:30 | `127.0.0.1:8000/console/models`, after operator token is privately entered | Inspect pinned digest, approved installed model and local load status; no model downloaded by the demo |
| 01:30–02:00 | Approved model chat and deployment lifecycle | Demonstrate actual output and Stop/Start/Promote/Rollback only when current hardware and model state have already passed the P1 checks |
| 02:00–02:30 | Public sanitized [experimental case study](PORTFOLIO_CASE_STUDY.md) | The FIFO scheduler raises admitted completions under overload but worsened successful goodput and p95/TTFT; a test can pass even when performance trade-offs are negative |
| 02:30–03:00 | [P1–P6 acceptance](PORTFOLIO_V1_ACCEPTANCE.md) and next steps | Explain bounded Go-path candidate, real Mac A/B/A/B still pending, security boundaries and why v1 has **not** been tagged |

Do not screen-record bearer tokens, Google identity attributes, private
prompts/responses, local DSNs, raw reports, browser cookies or invitees.
Capture only owner-approved screenshots/video after independent actual
browser walkthrough. **This document is not evidence that a video exists.**

## Questions an interviewer is likely to ask

**Q: Why Go for the serving gateway and Python for the control plane?**

The bounded HTTP streaming path, cancellation, queue and Go race-tested
concurrency are isolated from Python's model management, migrations, JWT
verification and experiment tools. This is a pragmatic engineering split,
not proof that Go makes Ollama's model compute faster.

**Q: What is your optimization?**

The best *measured* Go CPU-side improvement currently concerns single-pass
canonical model selection versus repeated JSON decoding in a hosted
synthetic benchmark, **not real model generation**. A separate opt-in
4 KiB SSE relay read-buffer candidate preserves the old 1 KiB default
and has source/CPU tests, but **no measured live speedup**. Both must
remain distinct from Ollama's own `OLLAMA_NUM_PARALLEL` setting.

**Q: What do the real benchmarks say?**

The October 13 configuration test showed an observed concurrency-2
throughput increase but regression at concurrency 1; changing
`OLLAMA_NUM_PARALLEL` is Ollama configuration, not a Metriqen algorithm.
The October 20 A/B/A/B study recorded 1,296 successful requests but
had substantial drift and reversed effects. The October 21 FIFO
queue-depth policy increased completed admissions from **144/432** to
**252/432**, while successful goodput declined at every tested
concurrency and overloaded p95/TTFT worsened. These are descriptive,
operator-reported local measurements, with outstanding thermal/slot
attestation and independent acceptance. No causal serving speedup
is verified.

**Q: What exactly is TTFT?**

Time to first observed streamed token/event on the client. It is not
the model's internal prefill time, and the benchmark's client-observed
TTFT includes admission wait, gateway/network processing and runtime
behavior. Nonstream requests do not expose streaming TTFT.

**Q: How do you handle overload?**

The Go gateway has bounded process-local active admissions and
opt-in bounded FIFO queueing with wait deadlines, explicit 429
rejections, cancellation-safe handoff and counters. A gateway slot
does **not** equal a GPU model-execution slot. Queueing improves one
completion outcome under offered overload but may increase latency;
the project reports this drawback.

**Q: Is the API multi-tenant and secure?**

A strictly **opt-in** protected Go inference mode supports verified
project identity, role/membership decisions, approved deployments and
shared Redis limits. The default developer gateway and operator UI
remain bound to loopback and are **not production public APIs**.
The local operator token is not an OIDC subject. A real hosted
two-account Google login, HTTPS BFF, deployed Redis/PostgreSQL and
tenant-isolation security assessment remain outstanding.

**Q: Can Metriqen autoscale GPUs?**

No. It has provider-neutral, offline worker-sizing policy plus an
in-memory, revision-fenced **simulator**, with no cloud provider
adapter, actual GPU worker, enforced billing cap or measured scale
events. Never describe simulator output as deployed autoscaling.

**Q: How do you validate answer quality?**

The current evaluation checks an intentionally narrow 36-question
reference set with exact/normalized matching. This is not a robust
semantic, factuality or hallucination benchmark. A quality-preserving
serving improvement requires independently reviewed matched
real-model generations and explicit error/coverage analysis.

**Q: Why isn't v1.0 tagged despite green CI?**

The release checklist requires P1–P6. Current source tests,
ephemeral PostgreSQL/Redis integration and Go race checks demonstrate
specific contracts but cannot attest real Safari/mobile accessibility,
model rollback observations, runtime power/slots, a reproducible
Metriqen-owned speedup, approved license or authentic screenshots.
See the [fail-closed read-only readiness audit](operations/portfolio-release-readiness.md).

## Questions to avoid overclaiming

Do **not** say "GPU autoscaling is deployed", "production Google login
works", "I improved model inference by 35.9%", "all requests became
faster", "CI proves my Mac benchmark" or "Metriqen is production ready".

Instead explain *which specific CPU or serving policy behavior* was
measured, where the measurement occurred, how large the sample was,
what degraded and which acceptance evidence is still missing.

## Primary evidence links

- [Current source/evidence matrix](PORTFOLIO_CURRENT_STATUS.md)
- [Engineering case study](PORTFOLIO_CASE_STUDY.md)
- [P1–P6 release acceptance](PORTFOLIO_V1_ACCEPTANCE.md)
- [Go model-selection hosted CPU benchmark](benchmarks/experiments/2026-10-18-model-selection-cpu.md)
- [October 20 A/B/A/B drift report](benchmarks/experiments/2026-10-20-ollama-parallelism-abab.md)
- [October 21 FIFO trade-off](benchmarks/experiments/2026-10-21-fifo-admission-overload.md)
- [Optional SSE buffer candidate](benchmarks/sse-relay-buffer-candidate.md)
- [Public deployment limits](security/public-deployment-boundaries.md)
