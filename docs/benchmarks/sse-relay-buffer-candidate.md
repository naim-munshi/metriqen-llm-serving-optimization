# SSE relay read-buffer candidate: bounded 1 KiB → 4 KiB

**Status: opt-in code candidate only — no proven Mac/Metal/Ollama speedup.**

## Hypothesis (predeclared)

The Metriqen Go gateway currently reads the upstream SSE response in a
1,024-byte buffer and immediately forwards + flushes each successful read.
A 4,096-byte bounded buffer might reduce `Read`/`Write`/HTTP flush calls
and per-byte tracker overhead when Ollama delivers **buffered bursts**
larger than 1 KiB. With small real-time token deltas this may make **no
difference**. `io.Reader.Read` is not a fill-exactly call: retaining
a flush after *every returned read* should preserve first-available-byte
behavior.

**Potential regression:** an individual stream can now temporarily hold
3 KiB more transient buffer memory; burst sizes and Go read/coalescing
may affect TTFT and p95. No latency, usage, correctness or throughput
improvement is presumed.

## Isolated patch

- File: `services/inference-gateway/internal/httpapi/inference.go`
- Baseline: `make([]byte, 1024)` on main commit `129bba5`
- Default (unchanged): `METRIQEN_SSE_RELAY_BUFFER_BYTES` unset = 1 KiB.
- Candidate (explicit startup opt-in only):
  `METRIQEN_SSE_RELAY_BUFFER_BYTES=4096` = 4 KiB.
  Unrecognized values fail server initialization instead of guessing.
- Same per-read `w.Write`, `flusher.Flush`, streaming cancellation,
  8 MiB total cap, SSE terminal framing, provider usage and all
  model/authorization/queueing controls.
- **No** change to Ollama, GPU model/kernel, batching, queue depth,
  decoding, model weights or concurrency configuration.

## Checks possible with no AC power or model

CI runs Go tests/race, exact startup opt-in/default rejection, SSE first-event flush and cancellation tests,
bounded stream protection, terminal `[DONE]`, usage classification,
and a new synthetic HTTP byte-identity test. The paired CPU-only
benchmark compares 1024 and 4096 with **identical synthetic SSE bytes**:

```bash
cd services/inference-gateway
GOMAXPROCS=1 go test -run '^$' \
  -bench '^BenchmarkSSEChunkedRelayParsing$' \
  -benchtime=250ms -count=5 -benchmem -cpu 1 ./internal/httpapi
```

This measures Go memory-buffer copy/framing **only**. It is not
a real endpoint inference benchmark; it cannot demonstrate a change
in real TTFT/p95, generation speed, memory pressure or goodput.
The main-source CI benchmark is descriptive and runner-dependent.

## Later, on AC-powered Mac only

1. Compare the default (unset env = 1 KiB) and candidate
   (`METRIQEN_SSE_RELAY_BUFFER_BYTES=4096`) Go gateway startup modes using the **same** pinned
   installed Ollama digest, same model residency, runtime version,
   quantization, power/thermals, prompt dataset, SSE max token cap,
   true Ollama parallel slots, queue depth, auth, offered concurrency,
   and single/constant source of background load.
2. Reuse the existing controlled `tools.benchmark` evidence workflow,
   collecting fresh A/B/A/B or randomized repeated source versions on
   the **same gateway endpoint**. Inspect actual process binary SHA
   for each phase: `git pull` alone does **not** restart the running
   Go gateway or reload a compiled binary.
3. Record success/failure rates **including rejected/429 requests**,
   successful goodput, p50/p95 latency, p50/p95 first-content TTFT,
   provider usage coverage, matched 36-reference quality outcomes,
   and operator-inspected thermal/power/slot notes.
4. Reject claims based on a single favorable run, a Go CPU microbenchmark,
   an unverified upstream, or changed model/runtime configuration.
   Preserve even *negative* results. A modest buffer tweak is not
   meaningful enough to claim as proven optimization until effect
   replicates and exceeds measurement variability.
5. Prefer rollback to the original 1 KiB buffer if p95 TTFT increases,
   quality fails, SSE `[DONE]`/usage differs, crash/race appears,
   or the real experiment shows no meaningful reproducible benefit.

Reference: [P3 acceptance contract](../PORTFOLIO_V1_ACCEPTANCE.md),
[October 20 drift](experiments/2026-10-20-ollama-parallelism-abab.md),
[operator final Mac handoff](../operations/portfolio-final-mac-handoff.md).
