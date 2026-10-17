# Durable project deployment desired state (development-only)

This milestone adds a **PostgreSQL-backed, tenant-scoped deployment control
plane**. It is entirely distinct from the earlier `/api/v1/local/deployments`
SQLite aliases and does not modify the existing local model manager. No model
is pulled, verified, loaded, deployed, health-checked, or scaled by these new
endpoints. They accept a *requested* model tag + digest only.

## Data model and consistency

Migration `0005_project_deployment_desired_state.sql` adds:

- `project_deployments`: project + organization composite foreign key;
  unique `(project_id, name)`; active and previous desired model revision;
  desired running/stopped flag and strictly increasing generation; timestamps.
- `project_deployment_commands`: one durable, uniquely indexed command
  receipt per project + exact verified issuer + subject + Idempotency-Key.
  It stores the command payload hash and exact original response snapshot for
  safe retries, plus actor identity, action and date.

**Observed state is initially `unreconciled`, generation 0** on each
new desired generation. An **explicit, separately launched trusted local
operator reconciler**, available after migration `0006`, can record a
point-in-time observation such as `ready`, `disabled`, `blocked` or
`failed`. A `ready` snapshot means only that a model reported as
installed and loaded at the pinned digest by the **loopback Ollama API
at observed_at**. This is not continuous serving health, authorization of a
model through the Go gateway, a GPU deployment, or a guarantee that the model
will remain loaded. A `disabled` observation means the operator asked this
deployment to stop and no preload occurred; it does **not** unload shared
Ollama model weights or block direct gateway inference.

Every mutation runs in one SQL transaction: lock authoritative `projects`
row `FOR UPDATE`, re-read exact issuer+subject membership and project grant
under a read lock, verify `deployment.manage`, check idempotency, compare
expected generation, update desired state, then insert the command receipt.
A SQL failure during receipt insertion rolls back the deployment change.
This serializes concurrent requests across API replicas sharing PostgreSQL.
Creation is capped at 20 deployment names per project. Reused idempotency
keys return the *original response snapshot*, even if desired state later
advanced; the same key with a different action/target/body returns 409.
A revoked user cannot retrieve a successful historical idempotency response
because authorization is checked before replay.

## Authenticated HTTP API

All routes require a verified dedicated OIDC `at+jwt`; roles, project
membership and grants are **never accepted from JWT custom claims**.
Project scope resolves from the authoritative database; cross-tenant/unknown
resources are uniformly HTTP 404. Owner/admin can manage any project in
their organization; deployment operators with an explicit project grant
may manage; viewer/developer/ML-engineer/auditor only have read access if
their role and grant permit it. No browser BFF route is added, so do not
pass a bearer token through untrusted browser JavaScript.

| Method | Endpoint | Semantics |
|---|---|---|
| GET | `/api/v1/projects/{project_id}/deployments` | List authorized desired state |
| GET | `/api/v1/projects/{project_id}/deployments/{name}` | Inspect desired/observed state |
| POST | `/api/v1/projects/{project_id}/deployments` | Create, name + model_tag + model_digest |
| POST | `/api/v1/projects/{project_id}/deployments/{name}/promote` | New model tag/digest with expected_generation |
| POST | `/api/v1/projects/{project_id}/deployments/{name}/rollback` | Swap previous desired model revision, compare generation |
| POST | `/api/v1/projects/{project_id}/deployments/{name}/stop` | Request stopped, compare generation |
| POST | `/api/v1/projects/{project_id}/deployments/{name}/start` | Request running, compare generation |

Every POST requires **one** header
`Idempotency-Key: <16–80 ASCII letters, numbers, hyphen or underscore>`.
New key per *new* action; reuse identical key for *retrying the exact
same action*. A changed payload on the same key produces 409.
Generation is a strict positive integer: stale clients receive 409.
Model tag validation supports limited plain names such as
`llama3.2:1b`, while model digest must be exactly 64 lowercase SHA-256 hex
characters. The requested digest is not treated as verified by the server
and is never used to pull or run untrusted model code in this milestone.

## Operator-only installation and validation

1. Keep the control API and database on **127.0.0.1**. No public binding,
   proxy-forwarded bearer tokens, exposed environment secrets or automated
   startup DDL.
2. Back up PostgreSQL. Apply migrations `0001`, `0002`, `0003`,
   `0004`, `0005`, **then `0006`**, as a privileged migration operator.
3. Configure the pinned OIDC public JWKS/issuer/audience/client and trusted
   loopback `METRIQEN_DATABASE_URL` as documented for the existing
   PostgreSQL-backed project API. Provision real membership and grants
   through the existing authorized team/project workflow.
4. Use a real dedicated access token in a trusted local client; create a
   desired deployment and verify readback persists across control API restart.
   Retry the same key; verify no duplicate row. Promote and rollback with
   generation checks. Remove the project grant and verify the unchanged
   token now receives 404. Confirm missing DB responds 503.
5. For manual local reconciliation, follow the
   [operator runbook](deployment-reconciler.md). The worker enforces
   operator-approved digest allowlists, a fixed local Ollama origin and
   bounded attempts/leases, but is not a secure model serving orchestrator.
6. Before production serving, implement robust signed model artifact
   provenance, actual model-to-project routing enforcement, guaranteed
   unloading/isolation, production worker attestation, consistent health
   intervals, tenant quotas, TLS/IdP, retries/alerts and autoscaling.

CI exercises deterministic SQLite HTTP/security tests and **isolated real
PostgreSQL 16 migration 0001→0006**, including concurrent independent
connections racing on idempotency and generation. These are not tests of an
actual deployed IdP, model runtime, GPU, Mac PostgreSQL, TLS or production
recovery. This is a durable *intent/command journal plus explicitly invoked local
observation pass*, not a completed deployment orchestrator.
