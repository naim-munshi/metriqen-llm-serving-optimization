# Contributing to Metriqen

Metriqen is a research-oriented local serving prototype. Changes should prioritize **correctness, reproducibility, security boundaries and reviewability** over large unverified feature claims.

## Development setup

Python 3.12+ and Go 1.23+ are required for the current tools. Ollama and Docker are only needed for their respective local workflows.

From the repository root:

```bash
cd services/control-api
python -m pip install -e '.[dev]'
cd ../..

make test
make format-check
```

The targets cover the Python control API, Go gateway (race-enabled), cross-service E2E tests, benchmark contracts and NLP evaluation. No Ollama model needs to run for the automated tests. Go formatting is enforced in CI. Python syntax checks are also enforced.

## Branch and pull-request workflow

1. Start a short-lived, descriptive branch from the latest `main` (for example, `feat/request-metrics` or `docs/reproduce-local-study`).
2. Make one focused, reviewable change and include relevant regression tests.
3. Use concise commit/PR titles with a scope where useful: `feat(gateway): …`, `fix(benchmark): …`, `docs: …`, `test(gateway): …`.
4. In the pull request, explain the change, security/reproducibility risks and how it was tested. Note known limitations and any missing real-device validation.
5. Require the applicable CI suites to pass before merging. Prefer squash merge for a focused PR. Delete merged topic branches after verifying the PR is merged; never force-rewrite `main` just to make history look different.

The project currently does **not** advertise mandatory branch protection or a guaranteed external review process; CI status is visible in GitHub Actions. See the [PR template](.github/PULL_REQUEST_TEMPLATE.md).

## Evidence integrity

- Never commit `reports/local/`, secrets, raw sensitive prompts, model completions, credentials or private hardware details. Preserve original local evidence without modifying failed runs or discarding inconvenient observations.
- Separate **synthetic CI fixtures** from **operator-recorded real inference** and from **independently reproduced results**.
- Record model tag **and digest**, precision/quantization, runtime version, hardware, power conditions, concurrency, prompt/output limits, dataset hashes, repetitions, streaming mode, errors and token-usage coverage.
- Do not call sequential before/after observations a causal speedup or imply reference string match measures semantic correctness.
- Keep queue or scheduler experiments isolated from the historical parallelism-only study: any new gateway setting is a confounder unless independently controlled.
- Runbooks: [local inference](docs/operations/local-inference.md), [repeated benchmark](docs/benchmarks/repeated-sweep.md), [real-model evidence](docs/benchmarks/real-model-evidence-bundle.md) and [security](SECURITY.md).

## Documentation and security

Use concise technical English with accurate implemented-vs-planned status. Do not add fabricated screenshots, cloud deployments, GPUs, stars, test counts, CI passes or benchmark figures. Favor a runnable command and a link to the primary evidence.

This gateway has no authentication on its inference endpoint. **Do not bind it to a public interface or process untrusted external requests.** Report suspected security issues privately if vulnerability reporting is enabled; do not disclose credentials in public issues.

## Licensing note

The original Metriqen repository software and documentation are distributed under the [MIT License](LICENSE), Copyright © 2026 Md Naim, where the contributor/copyright holder has the rights to grant that license. By proposing a contribution for inclusion, confirm that you have permission to submit it on MIT-compatible terms; do not submit third-party code, datasets, images, model weights or other assets whose license is incompatible or unknown. MIT is a software copyright license, not an authorization to publish private reports, prompts, user data, provider tokens or third-party model files. See [third-party boundaries](docs/operations/licensing-and-third-party.md).
