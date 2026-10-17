"""Explicit opt-in local reconciler for approved, digest-matched Ollama models.

This is a bounded operator process, NOT a public API or an always-on daemon.
A ready observation means 'the model was in Ollama /api/ps at the matching
digest at observed_at', not that project routing or future health is assured.
"""
from __future__ import annotations

import json
import os
from typing import Protocol

from app.local_models import DIGEST, MODEL_TAG, OllamaRuntime
from app.storage.reconciliation import SQLReconciliationStore, ReconciliationClaim

ALLOWLIST_ENV = "METRIQEN_RECONCILER_MODEL_ALLOWLIST"
ENABLED_ENV = "METRIQEN_RECONCILER_ENABLED"


class ModelRuntime(Protocol):
    def installed(self) -> list[dict]: ...
    def loaded(self) -> list[dict]: ...
    def preload(self, tag: str) -> None: ...


def load_allowlist(raw: str | None) -> dict[str, str]:
    """Only operator-owned pinned revisions are eligible for model loading."""
    if not isinstance(raw, str) or len(raw) > 16384:
        raise ValueError("missing or oversized trusted model allowlist")
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("invalid trusted model allowlist") from None
    if not isinstance(parsed, dict) or not 1 <= len(parsed) <= 50:
        raise ValueError("trusted model allowlist must have 1–50 pinned models")
    for tag, digest in parsed.items():
        if (not isinstance(tag, str) or MODEL_TAG.fullmatch(tag) is None
                or not isinstance(digest, str) or DIGEST.fullmatch(digest) is None):
            raise ValueError("trusted model allowlist contains invalid model identity")
    return parsed


class DeploymentReconciler:
    """One bounded pass, optionally invoked periodically by a local operator."""

    def __init__(self, store: SQLReconciliationStore,
                 allowed_models: dict[str, str],
                 runtime: ModelRuntime | None = None):
        if not isinstance(store, SQLReconciliationStore):
            raise TypeError("trusted reconciliation SQL store required")
        if not isinstance(allowed_models, dict) or not allowed_models:
            raise ValueError("operator model allowlist required")
        # Revalidate even if programmatically constructed.
        self.allowed_models = load_allowlist(json.dumps(allowed_models))
        self.store = store
        self.runtime = runtime if runtime is not None else OllamaRuntime()

    @staticmethod
    def _lookup(models: list[dict], tag: str) -> str | None:
        # Duplicate names or untrusted digest syntax must never be accepted
        # as proof that a particular desired revision was installed/loaded.
        matches = [m for m in models if isinstance(m, dict)
                   and m.get("tag") == tag]
        if len(matches) > 1:
            raise ValueError("ambiguous model identity")
        if not matches:
            return None
        digest = matches[0].get("digest")
        if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            raise ValueError("invalid model digest from runtime")
        return digest

    def probe(self, claim: ReconciliationClaim) -> tuple[str, str | None]:
        if claim.desired_state == "stopped":
            # Logical desired state ONLY. Do not unload a globally shared
            # Ollama model or imply the direct Go gateway is disabled.
            return "disabled", None
        if claim.desired_state != "running":
            return "failed", "runtime_unavailable"
        if self.allowed_models.get(claim.model_tag) != claim.model_digest:
            return "blocked", "not_allowlisted"
        try:
            installed = self._lookup(self.runtime.installed(), claim.model_tag)
            if installed is None:
                return "blocked", "model_missing"
            if installed != claim.model_digest:
                return "blocked", "digest_mismatch"
            loaded = self._lookup(self.runtime.loaded(), claim.model_tag)
            if loaded is not None and loaded != claim.model_digest:
                return "blocked", "loaded_digest_mismatch"
            if loaded is None:
                # No pulls: only the exact operator-allowed and installed
                # digest is eligible. Ollama tag mutation between checks is
                # still possible and must be guarded again after preload.
                self.runtime.preload(claim.model_tag)
                installed = self._lookup(self.runtime.installed(), claim.model_tag)
                loaded = self._lookup(self.runtime.loaded(), claim.model_tag)
                if installed != claim.model_digest or loaded != claim.model_digest:
                    return "failed", "preload_unverified"
            return "ready", None
        except Exception:
            # Never persist request bodies, model runtime error strings,
            # database URLs, network details, token material or prompts.
            return "failed", "runtime_unavailable"

    def run_once(self, *, limit: int = 10) -> dict[str, int]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("one reconciliation pass supports 1–20 claims")
        summary = {"claimed": 0, "ready": 0, "disabled": 0,
                   "blocked": 0, "failed": 0, "stale": 0}
        visited = set()
        for _ in range(limit):
            claim = self.store.claim_one(excluded=frozenset(visited))
            if claim is None:
                break
            visited.add(claim.id)
            summary["claimed"] += 1
            state, reason = self.probe(claim)
            if self.store.finish(claim, state, reason):
                summary[state] += 1
            else:
                summary["stale"] += 1
        return summary


def main() -> int:
    """Run explicitly: python -m app.reconciliation; no HTTP worker endpoint."""
    if os.environ.get(ENABLED_ENV) != "1":
        raise SystemExit("reconciliation is disabled (explicit opt-in required)")
    from app.storage.runtime import load_local_project_store
    db = load_local_project_store()
    if db is None:
        raise SystemExit("trusted local PostgreSQL must be configured")
    allowlist = load_allowlist(os.getenv(ALLOWLIST_ENV))
    worker = DeploymentReconciler(
        SQLReconciliationStore(db._engine), allowlist, OllamaRuntime(),
    )
    # No prompt/model identities/credentials printed in worker summaries.
    print(json.dumps(worker.run_once(limit=10), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
