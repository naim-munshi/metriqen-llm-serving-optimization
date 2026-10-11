# Local inference end-to-end contract verification

This suite launches a real Go gateway process and runs the Python benchmark
CLI against it via localhost. The upstream is a deterministic fixture
server, not a real language model. Fake latency and token counts are not
performance measurements.

## Acceptance criteria

- Streaming SSE responses reach the benchmark client; the first content
  delta produces TTFT and reported usage is aggregated correctly.
- Non-streaming responses report token usage without inventing TTFT.
- Upstream HTTP 429 is counted as a failure, not successful throughput.
- No prompts, generated content or upstream error bodies appear in reports.
- Child processes and fixture servers are shut down after tests.

Run from repository root with Python 3.12+ and Go 1.23+:

    python -m unittest discover -s tests/e2e -v

GitHub Actions runs the same test as the local-inference-e2e job.
Real Ollama integration, vLLM/GPU performance, production authentication and
multi-tenant isolation remain unverified and outside this test's scope.

NLP evaluation integration also runs the localhost-only quality CLI through a
real Go gateway process against the fixture upstream, verifying exact matching,
aggregate-only privacy and rejection of failed generations without partial reports.
Fixture answer scores are contract-test values, **not real model quality**.
