# Explicit local deployment reconciler (PostgreSQL / Ollama)

**Status: opt-in and development-only.** This is an operator-invoked local
worker that confirms the installed/loaded identity of **already locally
installed** Ollama models and can preload a model only when it matches a
distinct operator-controlled allowlist. It records a *snapshot*, not
continuous health or a publicly usable deployment. It is NOT invoked at
FastAPI startup and adds **no unauthenticated HTTP endpoint**.

## Trust and prerequisites

1. Keep PostgreSQL, FastAPI, Go and Ollama on `127.0.0.1`; never expose
   model operations or database credentials publicly. Back up the local
   database and manually apply migrations **0001–0006 in order** with a
   privileged migration role. API startup and the worker NEVER execute DDL.
2. Existing project deployment write API still requires verified OIDC and
   current tenant `deployment.manage` rights. Set desired state using the
   API first. Do not use the public browser to expose JWTs or worker secrets.
3. Give the separately launched worker a **trusted local database role**
   permitted to read deployment desired state and update only reconciliation
   columns. It must never accept DB URLs, model tags or URLs from HTTP users.
   The existing local runtime loader enforces a pinned loopback PostgreSQL
   DSN. The worker uses `OllamaRuntime`, whose origin is hard-coded
   `http://127.0.0.1:11434`; proxies and redirects are disallowed.
4. Approve exact tag/digest pairs as private operator configuration. Use
   the 64-character lowercase SHA-256 digest returned by local `/api/tags`
   and cross-check with the expected installed artifact. Do not copy a
   client-supplied digest into the allowlist without independent review.

Example environment values are **placeholders, not working credentials**:

```sh
export METRIQEN_DATABASE_URL='postgresql+psycopg://LOCAL_WORKER:REPLACE_WITH_SECRET@127.0.0.1:5432/metriqen_dev'
export METRIQEN_RECONCILER_ENABLED=1
export METRIQEN_RECONCILER_MODEL_ALLOWLIST='{"llama3.2:1b":"REPLACE_WITH_REAL_64_CHAR_LOWERCASE_DIGEST"}'
cd services/control-api
python -m app.reconciliation
```

Only an exact installed digest, allowed tag and verified loaded digest can
produce `ready`. The process runs **one bounded batch of up to 10 claims**
and exits with aggregate counts; an operator may schedule further passes
with a separate local scheduler. No background service is enabled by code
or by these instructions. Missing/invalid configuration stops the process.
No prompts, bearer tokens, identities, database passwords, runtime response
bodies or model text are printed/persisted by this worker.

## Crash, retry and race behavior

- PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` ensures independent
  worker replicas do not claim the same eligible deployment simultaneously.
  Each worker claim has a **180-second expiring lease**. A crashed worker
  can be reclaimed after expiration; an expired worker cannot publish an
  observation once its lease has been replaced.
- Per desired generation, no more than **3 attempts** are permitted. A
  blocked/failed observation gets a **10/20-second backoff** for retries.
  Further automatic retries stop after attempt 3. An authorized new desired
  generation resets the attempts and lease, making the new revision eligible.
- Updating desired state immediately invalidates old observed status,
  observation timestamp, retry and outstanding worker claim. Publishing an
  observation requires matching both the lease UUID and current generation;
  a late worker cannot mark a newer revision ready.
- `ready` means installed+loaded exact digest was returned by local Ollama
  at `observed_at`. Ollama may unload later; this is **not health leasing**.
  `disabled` means desired routing disabled, with **no global unload**.
  It does not block inference against the same tag through the legacy or
  guarded direct Go gateway. `blocked` means missing/changed/unapproved
  model. `failed` means runtime unavailable or post-load proof failed.
  Persisted `observed_code` is a fixed generic category, never a runtime
  exception or connection string.
- The worker does not pull untrusted models. It confirms the installed
  digest before preloading and re-reads installed+loaded identities after.
  **A locally malicious process could retag a model between verification
  and preload**; the resulting observation is refused, but preload could
  still have loaded that other revision. Artifact immutability/signatures
  and protected runtimes are future production prerequisites.

## Scope of verification

CI tests use synthetic in-process Ollama for successful preloading, changed
digest, unavailable runtime, backoff, stopped-state safety and stale
completion. A **dedicated disposable PostgreSQL 16 CI run** applies
0001→0006 and tests independent concurrent worker claims, lease expiry,
generation changes and the command journal. It does **not** prove that this
worker ran on the user's Mac, downloaded or served a model, attained a
particular throughput, enforced tenant-specific live inference, or passed
production identity/security readiness.

**Critical non-goals:** No continuous real-time health tracking,
Kubernetes deployment or autoscaling, isolated GPU workers, durable artifact
signatures, separate runtime tenant isolation, service-to-service mTLS,
worker RLS, token budgets, or protection against a compromised local
operator/DB role. Direct gateway routes must be secured independently.
