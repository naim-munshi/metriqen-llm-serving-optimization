# Local deployment routing (operator-only)

**Status:** local inference *routing aliases*, not production GPU/container
deployment orchestration. This capability adds actual named routing, model
promotion and one-step rollback to the existing localhost FastAPI control
plane while retaining the existing Go gateway for inference.

## Workflow from the browser

Use the existing
[opt-in local Model Manager](local-model-registry.md)
at http://127.0.0.1:8000/console/models. Connect with the private operator
token. First register and approve the desired model in the local registry,
then Load it into Ollama memory.

Under **04 / Model Routing**, enter a lowercase deployment name such as
`assistant`, choose an approved **loaded** model and click Create deployment.
The UI now shows the fixed alias, active digest, health-readiness state and
a prompt field.

- **Run deployment inference** sends prompt to `POST
  /api/v1/local/deployments/{name}/chat`, with a stable name regardless of
  which model is active. The control API looks up the current pinned tag,
  verifies approval and the installed/loaded digest, and forwards a bounded
  single-turn request to the fixed loopback Go gateway. The local response
  adapter also rejects replies whose reported `model` is absent or differs
  from the selected tag (sanitized HTTP 503), preventing false model labels.
  This is not cryptographic proof of the model executing on the GPU.
- **Promote** switches to a different already-registered, approved and loaded
  model. The previous active tag and digest are saved atomically as the
  one-step rollback target.
- **Rollback** switches back to the previous revision **only if** that
  model remains registered, approved, installed and loaded at the pinned
  digest. If it has been unloaded, Load it first.
- **Stop routing** disables inference through this alias without unloading
  Ollama model weights, which might be used by other aliases or clients.
- **Start routing** checks that the active model revision is approved and
  loaded before re-enabling the alias. It also compare-and-swaps the full
  trusted deployment state under SQLite's write lock: if another operator
  changes model revision, rollback history or paused state during the
  readiness check, Start returns HTTP 409 without enabling that unverified
  revision. Stop remains unconditionally available to pause a route.

The deployment record is persisted in the *same private local SQLite file*
as the opt-in model registry, with a maximum of 20 named deployment
aliases. No prompts, generated text, user sessions or API keys are
persisted. Names use lowercase letters, numbers, hyphen and underscore,
begin with a letter and have a maximum of 32 characters.

## HTTP operator contract

All endpoints require the exact existing local bearer token and a
loopback caller; no public routes are enabled by default.

| Method | Local API path | Effect |
|---|---|---|
| GET | `/api/v1/local/deployments` | List aliases with live readiness |
| POST | `/api/v1/local/deployments` | Create alias from an approved loaded revision: `{"name":"assistant","tag":"llama3.2:1b"}` |
| POST | `/api/v1/local/deployments/{name}/promote` | Switch to a candidate: `{"tag":"mistral:latest"}` |
| POST | `/api/v1/local/deployments/{name}/rollback` | One-step return to the previous approved loaded revision |
| POST | `/api/v1/local/deployments/{name}/stop` | Disable traffic through alias |
| POST | `/api/v1/local/deployments/{name}/start` | Re-enable alias when exact revision is ready |
| POST | `/api/v1/local/deployments/{name}/chat` | Single-turn local chat: `{"prompt":"hello","max_tokens":128}` |

Database writes are transactionally serialized via SQLite immediate
transactions. Promotion also compares the complete trusted deployment
snapshot obtained **before** candidate readiness validation (active tag,
active digest, previous rollback tag/digest, enabled flag, update time)
with the row locked for writing. If another operator changes any of
those fields in between, promotion returns HTTP 409 and does **not**
overwrite the newer routing/rollback history. Rollback uses the same
complete-state comparison under the write lock, in addition to verifying
the previously pinned rollback tag and digest. A concurrent Stop/Start,
promotion or stale state during readiness validation returns HTTP 409
without changing active model or rollback history. An API-ready
indicator is a point-in-time snapshot, not proof of future model availability.
All alias writes (Create, Promote, Rollback, Stop and Start) now
construct the returned confirmation from the same locked SQLite write
transaction. Another operator cannot modify the row between the write
and the result snapshot; a later GET may legitimately show newer state.
Inference routes always verify the pinned model revision again.

**No remote model pulls, separate inference process creation, replica
management, autoscaling, GPU scheduling, Kubernetes, health reconciliation,
cost reporting, versioned rollout percentages or tenant-scoped API keys**
are implemented here. The separate Go gateway remains directly reachable
on loopback and does not enforce these named aliases or registry approvals.
Production inference should not use this local operator service.

## Browser timeout behavior

Local model preload and non-streaming inference through the fixed Go
gateway may legitimately take longer than a typical status/API GET.
The Model Manager allows up to **130 seconds** for these **specific**
long-running operations, consistent with the gateway relay's 125-second
upstream deadline. Status, model listing, approval, stop/start, promotion,
rollback and routine monitoring retain **10-second** request bounds.
This prevents Safari from aborting a valid inference solely because
generation takes more than 10 seconds. It does **not** change Go admission,
GPU scheduling, timeout guarantees or inference throughput.

## Validation

The new CI tests use synthetic Ollama and gateway fixtures for:
authentication, approved+loaded+digest checks, name uniqueness, bounded
persistence, stable alias inference, promotion, stop/start and rollback,
and input rejection. Real Mac/Ollama deployment behavior still requires
a local operator acceptance test; no performance speedup is implied.
