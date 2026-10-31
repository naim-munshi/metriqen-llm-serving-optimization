# Mac benchmark blockers — safe diagnosis and owner handoff (2026-10-31)

This document starts from the **actual owner-supplied Mac output**, not CI
or assumptions. In the last report, the developer Mac was **50% on battery**,
the Ollama server API returned **0.40.11**, the earlier CLI displayed a
**client 0.30.8 vs server 0.40.11** warning, the running Ollama process did
not expose an explicit `OLLAMA_NUM_PARALLEL` value, and **active Ollama
model slots were not verified**. The installed model digest matched the
read-only preflight expectation. `ollama ps` showed no resident model at
that moment; an unloaded model is not evidence that it was uninstalled.

**Current decision: BLOCKED for P2 and P3 measurements.** Historic
AC-contaminated A/B/A/B data and negative FIFO goodput results must not
be reused to claim a clean speedup. Neither GitHub CI nor this runbook
can remotely operate or attest the user's Mac.

## 1. Safe no-change diagnosis

Keep existing Ollama, the Go Gateway and FastAPI running. From the repo
root, run:

```sh
python3 -m tools.benchmark.mac_runtime_diagnostic
```

This command only reads fixed loopback status endpoints, AC status and
**the metadata of the single 11434 listener**. It does not download,
load, stop or restart models; it never sends inference; it prints **no
process environment, full executable path, raw prompt, digest or token**.
A return code 0 means **diagnostic printed**, NOT benchmark acceptance.
It deliberately keeps `benchmark_ready: false` even if its list of
diagnostic blockers is empty, because real runtime `n_slots` and
thermal/same-workload checks still require independent owner review.

When the CLI reports a client/server version mismatch, identify the
specific installed `ollama` binaries and the actual listener owner
*privately* before choosing how to update the CLI. Do not install a
random third-party binary, download new model weights, remove an Ollama
app, or launch a second `ollama serve` next to the existing listener
to hide the mismatch.

## 2. Unblock ONLY with owner-confirmed Mac conditions

1. Plug in the charger; verify `pmset -g batt` says **AC Power**.
   Do not start timed runs during battery/charge source transitions.
2. Confirm exactly one trusted Ollama listener is bound to
   `127.0.0.1:11434` and is the intended owner-controlled process.
   Check actual **server** version via `/api/version` and installed
   digest via `/api/tags`, not solely `ollama --version`.
3. For each P2 phase the **actual Ollama server process**, not just a
   Terminal's shell, must explicitly run with
   `OLLAMA_NUM_PARALLEL=1` (A1/A2) or `=2` (B1/B2). Inherited
   or app-launched defaults are **not a verified configured setting**.
   The existing strict `tools.benchmark.preflight` intentionally
   refuses an absent value, even if the model produces answers.
   Identify the owning service/supervisor first; if a planned
   configuration transition requires a restart, use **that owning
   supervisor** and preserve all in-flight user services and history.
   Do not kill unknown PIDs or disable bearer/tenant controls.
4. Observe real Ollama model-load logs, including **`n_seq_max` /
   `n_slots`** at the time of measurement, model residency/quantization,
   version and thermal/power stability. The Go Gateway's four
   admission slots are **not** Ollama worker slots.
5. When all facts are verified, collect **one** fresh P2 A1 phase
   following [guarded phase-runner](../benchmarks/phase-runner.md),
   then separately B1/A2/B2. Save each run under a fresh private
   `reports/local/` path; never re-label or overwrite an older report.
   Only after **four successful controlled phases**, run the strict
   offline `tools.benchmark.four_phase_review`.

## 3. Finish P3 only after P2 controls hold

For the independent Metriqen-owned optimization, use the already
implemented [opt-in SSE 1 KiB vs 4 KiB candidate](../benchmarks/sse-relay-buffer-candidate.md)
and the [exact same-binary AC A1/B1/A2/B2 protocol](../benchmarks/sse-abab-ac-operator.md).
Start with `python3 -m tools.benchmark.sse_abab plan` (no network).
This comparison explicitly requires controlled Go listener identity
and binary fingerprint, pinned digest, identical Ollama slots and
matched output quality. Never mistake a synthetic CPU microbenchmark
for an Ollama serving speedup. A negative result must be recorded
and the 1 KiB default retained; do not promote the 4 KiB candidate
without a real measured benefit/approved tradeoff.

**Other release gates remain open:** P4 true Safari 320px,
full keyboard/VoiceOver and owner-approved media; P5 actual 502
response-body inspection and any production features claimed; P6
GitHub admin-enforced main protection, final P1–P6 human approval and
release SHA CI. The owner already reported successful P1 lifecycle and
P5 real client-cancel recovery; do not repeat either merely to create
another PASS. `v1.0.0` must remain unreleased until acceptance.
