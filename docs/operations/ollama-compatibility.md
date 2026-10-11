# Ollama compatibility and evidence checklist

The Metriqen gateway forwards OpenAI-compatible chat requests to a **locally
installed** Ollama server. This checklist separates protocol support from
verified inference and from measured model performance.

## Compatibility matrix

| Contract | Expected behavior | Evidence required |
| --- | --- | --- |
| Nonstreaming chat | JSON response with nonempty choices | Actual response from pinned Ollama model |
| Streaming chat | SSE content deltas and [DONE] terminator | Observe actual content chunks via gateway |
| Error handling | Upstream rejection is not counted as successful inference | Explicit negative test |
| Prompt privacy | No prompts or completions in aggregate reports | Inspect output and logs |
| Completion usage | May be absent in streaming responses | Record actual usage coverage |
| Output token throughput | Only calculated when every successful request has completion usage | No fallback to invented token counts |
| TTFT | First nonempty content delta, not SSE role header | Compare runner semantics with actual SSE |
| Quality | Reference-string agreement only | Execute real model against fixed reference dataset |

## Known limitations

- CI fixture tests do not prove a real Ollama model is loaded or responding.
- A model tag is not necessarily an immutable digest; record the exact
  revision or model artifact fingerprint where available.
- Streaming usage can be absent depending on upstream/runtime behavior.
  In that case token throughput remains `null`; do not estimate it from
  characters or words.
- GPU memory, utilization, quantization configuration and inference cost
  are not measured by the current runner.
- The development gateway has no authentication and must stay on loopback.

## Acceptance evidence

1. Capture actual `ollama --version`, model tag/digest, OS and hardware.
2. Verify direct nonstream and streamed model responses.
3. Repeat through the gateway with a fixed workload.
4. Execute benchmark at concurrency 1, 2 and 4; repeat runs and retain
   failures, response usage coverage and timing spread.
5. Execute quality evaluation using the same pinned model.
6. Publish an evidence-backed comparison only after real-model runs, with
   model-quality caveats and without confidential prompts or outputs.

Until those steps have been executed, the real-model milestone is
**blocked on runtime access**, not completed by this checklist.
