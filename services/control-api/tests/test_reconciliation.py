"""Offline, deterministic deployment reconciliation security contracts.

All model IO is synthetic; no real Mac/Ollama/GPU connection is made.
PostgreSQL 16 cross-connection lease tests live in the dedicated CI module.
"""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.reconciliation import DeploymentReconciler, load_allowlist, main
from app.storage.durable_deployments import SQLDurableDeploymentStore
from app.storage.reconciliation import (
    SQLReconciliationStore, LEASE_SECONDS, MAX_ATTEMPTS,
)
from app.storage.schema import project_deployments
from test_durable_deployments import (
    deployment_env, become, create, change, auth, base,
    A, B, DA, DB,
)
from test_sql_project_store import seeded, engine, signing_config

ALLOWED = {A: DA, B: DB}


class FakeOllama:
    def __init__(self, *, installed=None, loaded=None, failure=False):
        self.installed_models = ([{"tag": A, "digest": DA}, {"tag": B, "digest": DB}]
                                 if installed is None else installed)
        self.loaded_models = [] if loaded is None else loaded
        self.preloads = []
        self.failure = failure

    def installed(self):
        if self.failure:
            raise RuntimeError("SECRET_OAUTH_AND_DATABASE_URL")
        return list(self.installed_models)

    def loaded(self):
        if self.failure:
            raise RuntimeError("SECRET_OAUTH_AND_DATABASE_URL")
        return list(self.loaded_models)

    def preload(self, tag):
        if self.failure:
            raise RuntimeError("SECRET_OAUTH_AND_DATABASE_URL")
        self.preloads.append(tag)
        match = next(m for m in self.installed_models if m["tag"] == tag)
        self.loaded_models = [*filter(lambda m: m["tag"] != tag, self.loaded_models), match]


def worker(engine, runtime, allowed=ALLOWED):
    return DeploymentReconciler(SQLReconciliationStore(engine), allowed, runtime)


def test_ready_is_real_digest_checked_snapshot_and_mutation_clears_it(deployment_env):
    client, engine, _, signing = deployment_env
    become(engine)
    state = create(client, signing).json()
    runtime = FakeOllama()
    reconcile = worker(engine, runtime)
    assert reconcile.run_once() == {
        "claimed": 1, "ready": 1, "disabled": 0,
        "blocked": 0, "failed": 0, "stale": 0,
    }
    assert runtime.preloads == [A]
    active = client.get(f"{base()}/stable", headers=auth(signing)).json()
    assert active["observed_state"] == "ready"
    assert active["observed_generation"] == 1
    assert active["observed_at"] is not None
    assert active["reconcile_attempts"] == 1
    assert active["observed_code"] is None
    assert reconcile.run_once()["claimed"] == 0
    # This is a point-in-time observation; a new desired revision must
    # invalidate it *before* a worker sees the new revision.
    promoted = change(client, signing, "promote", 1, "new-gen-promotion-001")
    assert promoted.status_code == 200
    assert promoted.json()["observed_state"] == "unreconciled"
    assert promoted.json()["observed_generation"] == 0
    assert promoted.json()["observed_at"] is None
    assert promoted.json()["reconcile_attempts"] == 0
    assert worker(engine, runtime).run_once()["ready"] == 1
    assert runtime.preloads == [A, B]
    stopped = change(client, signing, "stop", 2, "stop-gen-request-001")
    assert stopped.json()["observed_state"] == "unreconciled"
    assert worker(engine, runtime).run_once()["disabled"] == 1
    disabled = client.get(f"{base()}/stable", headers=auth(signing)).json()
    assert disabled["observed_state"] == "disabled"
    assert disabled["observed_generation"] == 3
    # No global unload of a model shared by other local projects.
    assert runtime.loaded_models
    assert runtime.preloads == [A, B]
    # Idempotent original command reply does not imply CURRENT state.
    assert create(client, signing).json() == state
    assert client.get(f"{base()}/stable", headers=auth(signing)).json() != state


def test_operator_pin_required_and_missing_changed_model_cannot_preload(deployment_env):
    client, engine, _, signing = deployment_env
    become(engine)
    assert create(client, signing).status_code == 201
    runtime = FakeOllama()
    assert worker(engine, runtime, {B: DB}).run_once()["blocked"] == 1
    observed = client.get(f"{base()}/stable", headers=auth(signing)).json()
    assert observed["observed_code"] == "not_allowlisted"
    assert runtime.preloads == []
    # A separate desired command resets bounded retry state immediately.
    assert change(client, signing, "stop", 1, "safety-stop-request-01").status_code == 200
    assert change(client, signing, "start", 2, "safety-start-request-01").status_code == 200
    mismatched = FakeOllama(installed=[{"tag": A, "digest": DB}])
    assert worker(engine, mismatched).run_once()["blocked"] == 1
    status = client.get(f"{base()}/stable", headers=auth(signing)).json()
    assert status["observed_code"] == "digest_mismatch"
    assert mismatched.preloads == []


def test_reconcile_after_preload_requires_new_installed_and_loaded_digest(deployment_env):
    client, engine, _, signing = deployment_env
    become(engine)
    create(client, signing)
    class TagChangedDuringPreload(FakeOllama):
        def preload(self, tag):
            self.preloads.append(tag)
            self.installed_models = [{"tag": tag, "digest": DB}]
            self.loaded_models = [{"tag": tag, "digest": DB}]
    runtime = TagChangedDuringPreload()
    summary = worker(engine, runtime).run_once()
    assert summary["failed"] == 1
    assert runtime.preloads == [A]
    row = client.get(f"{base()}/stable", headers=auth(signing)).json()
    assert row["observed_state"] == "failed"
    assert row["observed_code"] == "preload_unverified"
    assert row["observed_generation"] == 1


def test_failed_runtime_does_not_leak_secrets_and_retries_with_backoff(deployment_env):
    client, engine, _, signing = deployment_env
    become(engine)
    create(client, signing)
    runtime = FakeOllama(failure=True)
    reconciler = worker(engine, runtime)
    assert reconciler.run_once()["failed"] == 1
    row = client.get(f"{base()}/stable", headers=auth(signing)).json()
    assert row["observed_state"] == "failed"
    assert row["observed_code"] == "runtime_unavailable"
    assert "SECRET" not in str(row)
    assert row["reconcile_attempts"] == 1
    # Repeated invocations before the backoff expires cannot hammer runtime.
    assert reconciler.run_once()["claimed"] == 0
    store = SQLReconciliationStore(engine)
    t = datetime.now(timezone.utc) + timedelta(minutes=1)
    claim = store.claim_one(now=t)
    assert claim is not None and claim.attempt == 2
    assert store.finish(claim, "failed", "runtime_unavailable", now=t)
    claim = store.claim_one(now=t + timedelta(minutes=1))
    assert claim is not None and claim.attempt == MAX_ATTEMPTS
    assert store.finish(claim, "failed", "runtime_unavailable",
                        now=t + timedelta(minutes=1))
    assert store.claim_one(now=t + timedelta(days=1)) is None
    row = client.get(f"{base()}/stable", headers=auth(signing)).json()
    assert row["reconcile_attempts"] == MAX_ATTEMPTS
    # New authorized desired generation permits a fresh bounded retry cycle.
    changed = change(client, signing, "stop", 1, "new-intent-after-failure")
    assert changed.json()["reconcile_attempts"] == 0
    assert worker(engine, runtime).run_once()["disabled"] == 1


def test_crashed_worker_lease_expires_and_stale_result_is_rejected(deployment_env):
    client, engine, _, signing = deployment_env
    become(engine)
    create(client, signing)
    store = SQLReconciliationStore(engine)
    t0 = datetime.now(timezone.utc)
    original = store.claim_one(now=t0)
    assert original is not None and original.attempt == 1
    assert store.claim_one(now=t0 + timedelta(seconds=LEASE_SECONDS - 1)) is None
    replacement = store.claim_one(now=t0 + timedelta(seconds=LEASE_SECONDS + 1))
    assert replacement is not None and replacement.attempt == 2
    assert replacement.lease_id != original.lease_id
    assert not store.finish(original, "ready", now=t0 + timedelta(seconds=LEASE_SECONDS + 2))
    assert store.finish(replacement, "ready", now=t0 + timedelta(seconds=LEASE_SECONDS + 2))
    row = client.get(f"{base()}/stable", headers=auth(signing)).json()
    assert row["observed_state"] == "ready"
    assert row["observed_generation"] == 1
    assert store.claim_one(now=t0 + timedelta(hours=2)) is None


def test_stale_generation_worker_cannot_publish_ready_after_stop(deployment_env):
    client, engine, _, signing = deployment_env
    become(engine)
    create(client, signing)
    store = SQLReconciliationStore(engine)
    old = store.claim_one()
    assert old is not None
    stopped = change(client, signing, "stop", 1, "concurrent-stop-key-01")
    assert stopped.status_code == 200
    assert stopped.json()["observed_state"] == "unreconciled"
    assert not store.finish(old, "ready")
    assert worker(engine, FakeOllama(failure=True)).run_once()["disabled"] == 1
    state = client.get(f"{base()}/stable", headers=auth(signing)).json()
    assert state["desired_generation"] == 2
    assert state["observed_state"] == "disabled"


@pytest.mark.parametrize("bad", [
    None, "", "[]", "{}", "null", "{invalid", "[]{}",
    json.dumps({"NO/CANONICAL/URI": DA}),
    json.dumps({A: "A" * 64}),
    json.dumps({A: "not-a-digest"}),
    json.dumps({A: DA, B: 4}),
    "x" * 17000,
])
def test_untrusted_or_missing_operator_allowlist_fails_closed(bad):
    with pytest.raises(ValueError):
        load_allowlist(bad)
    assert load_allowlist(json.dumps(ALLOWED)) == ALLOWED


def test_invalid_completion_inputs_and_worker_opt_in(deployment_env, monkeypatch):
    client, engine, _, signing = deployment_env
    become(engine)
    create(client, signing)
    store = SQLReconciliationStore(engine)
    claim = store.claim_one()
    with pytest.raises(ValueError):
        store.finish(claim, "healthy")
    with pytest.raises(ValueError):
        store.finish(claim, "ready", "runtime_unavailable")
    with pytest.raises(ValueError):
        store.finish(claim, "failed", None)
    with pytest.raises(ValueError):
        store.finish(claim, "failed", "PRIVATE_CONNECTION")
    with pytest.raises(ValueError):
        worker(engine, FakeOllama()).run_once(limit=21)
    monkeypatch.delenv("METRIQEN_RECONCILER_ENABLED", raising=False)
    with pytest.raises(SystemExit, match="disabled"):
        main()


def test_missing_deployments_never_run_or_make_network_calls(deployment_env):
    client, engine, _, signing = deployment_env
    become(engine)
    runtime = FakeOllama(failure=True)
    result = worker(engine, runtime).run_once()
    assert result["claimed"] == 0
    assert client.get(base(), headers=auth(signing)).json() == {"deployments": []}
