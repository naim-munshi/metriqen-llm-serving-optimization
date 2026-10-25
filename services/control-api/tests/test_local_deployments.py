"""Offline local deployment routing contracts; no real model serving claimed."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.local_deployments import LocalDeploymentStore, MAX_DEPLOYMENTS
from app.local_models import configure_local_models

TOKEN = "local-deployment-synthetic-test-token-32characters"
A, B = "llama3.2:1b", "mistral:latest"
DIGEST_A, DIGEST_B = "a" * 64, "b" * 64


class LocalModels:
    def __init__(self):
        self.available = [{"tag": A, "digest": DIGEST_A},
                          {"tag": B, "digest": DIGEST_B}]
        self.in_memory = []
        self.unloads = 0

    def installed(self):
        return list(self.available)

    def loaded(self):
        return list(self.in_memory)

    def preload(self, tag):
        self.in_memory = [*filter(lambda m: m["tag"] != tag, self.in_memory),
                          next(m for m in self.available if m["tag"] == tag)]

    def unload(self, tag):
        self.unloads += 1
        self.in_memory = [m for m in self.in_memory if m["tag"] != tag]


class Gateway:
    def __init__(self):
        self.calls = []

    def complete(self, tag, prompt, max_tokens):
        self.calls.append((tag, prompt, max_tokens))
        return {"tag": tag, "content": "LOCAL_OK", "finish_reason": "stop"}


@pytest.fixture
def env(tmp_path):
    app = FastAPI()
    runtime, gateway = LocalModels(), Gateway()
    path = tmp_path / "models.sqlite3"
    configure_local_models(app, token=TOKEN, path=path,
                           runtime=runtime, gateway_chat=gateway)
    client = TestClient(app, client=("127.0.0.1", 12000))
    return client, runtime, gateway, path


def request(client, method, path, body=None, token=TOKEN):
    return client.request(method, path, json=body,
                          headers={"Authorization": "Bearer " + token} if token else {})


def prepare(client, runtime, tag):
    created = request(client, "POST", "/api/v1/local/models", {"tag": tag})
    assert created.status_code == 201, created.text
    approved = request(client, "POST", f"/api/v1/local/models/{tag}/approve")
    assert approved.status_code == 200, approved.text
    runtime.preload(tag)


def deploy(client, tag=A, name="prod"):
    return request(client, "POST", "/api/v1/local/deployments",
                   {"name": name, "tag": tag})


def test_full_deployment_promote_rollback_and_stable_chat(env):
    client, runtime, gateway, path = env
    assert request(client, "GET", "/api/v1/local/deployments").json() == {"deployments": []}
    prepare(client, runtime, A)
    created = deploy(client)
    assert created.status_code == 201, created.text
    assert created.json()["active_tag"] == A
    assert created.json()["active_digest"] == DIGEST_A
    assert created.json()["previous_tag"] is None
    assert created.json()["enabled"] is True
    uri = "/api/v1/local/deployments/prod/chat"
    assert request(client, "POST", uri, {"prompt": "hello", "max_tokens": 8}).json() == {
        "deployment": "prod", "tag": A, "content": "LOCAL_OK", "finish_reason": "stop"
    }
    prepare(client, runtime, B)
    switched = request(client, "POST", "/api/v1/local/deployments/prod/promote",
                       {"tag": B})
    assert switched.status_code == 200
    assert switched.json()["active_tag"] == B
    assert switched.json()["previous_tag"] == A
    assert request(client, "POST", uri, {"prompt": "hello", "max_tokens": 8}).json()["tag"] == B
    rolled = request(client, "POST", "/api/v1/local/deployments/prod/rollback")
    assert rolled.status_code == 200
    assert rolled.json()["active_tag"] == A
    assert rolled.json()["previous_tag"] == B
    assert request(client, "POST", uri, {"prompt": "again", "max_tokens": 5}).json()["tag"] == A
    assert gateway.calls == [(A, "hello", 8), (B, "hello", 8), (A, "again", 5)]
    assert LocalDeploymentStore(path).get("prod")["active_tag"] == A
    assert request(client, "GET", "/api/v1/local/deployments").json()["deployments"][0]["ready"]


def test_stop_blocks_chat_and_start_requires_active_revision(env):
    client, runtime, gateway, _ = env
    prepare(client, runtime, A)
    assert deploy(client).status_code == 201
    stopped = request(client, "POST", "/api/v1/local/deployments/prod/stop")
    assert stopped.status_code == 200 and not stopped.json()["enabled"]
    assert runtime.unloads == 0
    assert request(client, "POST", "/api/v1/local/deployments/prod/chat",
                   {"prompt": "hello"}).status_code == 409
    runtime.in_memory = []
    assert request(client, "POST", "/api/v1/local/deployments/prod/start").status_code == 409
    runtime.preload(A)
    assert request(client, "POST", "/api/v1/local/deployments/prod/start").json()["enabled"]
    assert request(client, "POST", "/api/v1/local/deployments/prod/chat",
                   {"prompt": "hello"}).status_code == 200


def test_requires_approved_installed_and_loaded_model(env):
    client, runtime, gateway, _ = env
    assert deploy(client).status_code == 404
    assert request(client, "POST", "/api/v1/local/models", {"tag": A}).status_code == 201
    assert deploy(client).status_code == 409  # unapproved
    assert request(client, "POST", f"/api/v1/local/models/{A}/approve").status_code == 200
    assert deploy(client).status_code == 409  # not loaded
    runtime.in_memory = [{"tag": A, "digest": "f" * 64}]
    assert deploy(client).status_code == 409
    runtime.in_memory = [{"tag": A, "digest": DIGEST_A}]
    assert deploy(client).status_code == 201
    runtime.available = [{"tag": A, "digest": "f" * 64},
                         {"tag": B, "digest": DIGEST_B}]
    assert request(client, "POST", "/api/v1/local/deployments/prod/chat",
                   {"prompt": "hello"}).status_code == 409
    assert gateway.calls == []


def test_rollback_refuses_absent_or_unavailable_previous(env):
    client, runtime, gateway, _ = env
    prepare(client, runtime, A)
    assert deploy(client).status_code == 201
    assert request(client, "POST", "/api/v1/local/deployments/prod/rollback").status_code == 409
    prepare(client, runtime, B)
    assert request(client, "POST", "/api/v1/local/deployments/prod/promote",
                   {"tag": B}).status_code == 200
    runtime.in_memory = [m for m in runtime.in_memory if m["tag"] != A]
    assert request(client, "POST", "/api/v1/local/deployments/prod/rollback").status_code == 409
    assert request(client, "GET", "/api/v1/local/deployments").json()["deployments"][0]["active_tag"] == B
    assert gateway.calls == []


@pytest.mark.parametrize("body", [
    {"name": "../escape", "tag": A}, {"name": "Bad", "tag": A},
    {"name": "a" * 33, "tag": A}, {"name": "prod", "tag": A, "host": "localhost"},
    {"name": "prod", "tag": "http://example.com"}, {"name": 23, "tag": A},
])
def test_invalid_deployment_payload_rejected(env, body):
    client, runtime, gateway, _ = env
    prepare(client, runtime, A)
    if body.get("name") == "prod":
        assert deploy(client).status_code == 201
    result = request(client, "POST", "/api/v1/local/deployments", body)
    assert result.status_code in (409, 422)
    assert len(request(client, "GET", "/api/v1/local/deployments").json()["deployments"]) <= 1


def test_operator_auth_precedes_deployment_storage(env):
    client, runtime, gateway, _ = env
    missing = request(client, "GET", "/api/v1/local/deployments", token="")
    assert missing.status_code == 401
    denied = request(client, "POST", "/api/v1/local/deployments",
                     {"name": "prod", "tag": A}, token="wrong")
    assert denied.status_code == 401
    assert request(client, "GET", "/api/v1/local/deployments").json() == {"deployments": []}
    remote = TestClient(client.app, client=("198.51.100.5", 50000))
    assert request(remote, "GET", "/api/v1/local/deployments").status_code == 403


def test_deployment_name_uniqueness_and_no_silent_revision_replacement(env):
    client, runtime, gateway, _ = env
    prepare(client, runtime, A)
    assert deploy(client).status_code == 201
    assert deploy(client).status_code == 409
    assert request(client, "POST", "/api/v1/local/deployments/prod/promote",
                   {"tag": A}).status_code == 409


def test_local_deployment_cap_prevents_unbounded_persistence(env):
    _, _, _, path = env
    store = LocalDeploymentStore(path)
    for n in range(MAX_DEPLOYMENTS):
        assert store.create(f"d{n}", A, DIGEST_A)["name"] == f"d{n}"
    with pytest.raises(Exception) as exc:
        store.create("overflow", A, DIGEST_A)
    assert "local deployment limit reached" in str(exc.value)


def test_promote_refuses_stale_pinned_state_without_overwriting_rollback(tmp_path):
    store = LocalDeploymentStore(tmp_path / "routing.sqlite3")
    store.initialize()
    assert store.create("acceptance-test", A, DIGEST_A)["previous_tag"] is None
    initial = store.get("acceptance-test")
    assert store.promote("acceptance-test", B, DIGEST_B,
                         expected_current=initial)["previous_tag"] == A
    switched = store.get("acceptance-test")

    with pytest.raises(Exception) as rejected:
        store.promote("acceptance-test", A, DIGEST_A,
                      expected_current=initial)
    assert getattr(rejected.value, "status_code", None) == 409
    assert store.get("acceptance-test") == switched

    # A start/stop change during model-readiness verification is also a
    # concurrent state change. Do not clobber it with a stale promotion.
    store.set_enabled("acceptance-test", False)
    stopped = store.get("acceptance-test")
    with pytest.raises(Exception) as rejected:
        store.promote("acceptance-test", A, DIGEST_A,
                      expected_current=switched)
    assert getattr(rejected.value, "status_code", None) == 409
    assert store.get("acceptance-test") == stopped


def test_promote_http_conflict_when_other_operator_switches_during_ready_check(env):
    client, runtime, gateway, path = env
    prepare(client, runtime, A)
    prepare(client, runtime, B)
    assert deploy(client).status_code == 201
    store = LocalDeploymentStore(path)
    original_loaded = runtime.loaded
    concurrent = {"ran": False}

    def competing_operator_promote():
        if not concurrent["ran"]:
            concurrent["ran"] = True
            first_snapshot = store.get("prod")
            store.promote("prod", B, DIGEST_B,
                          expected_current=first_snapshot)
        return original_loaded()

    runtime.loaded = competing_operator_promote
    # Simulates another authenticated operator changing the alias after the
    # HTTP request captured it, before candidate readiness completed.
    conflict = request(client, "POST", "/api/v1/local/deployments/prod/promote",
                       {"tag": B})
    assert conflict.status_code == 409
    assert concurrent["ran"]
    assert store.get("prod")["active_tag"] == B
    assert store.get("prod")["previous_tag"] == A
    assert gateway.calls == []


def test_rollback_rejects_stale_state_when_alias_paused_during_model_check(env):
    client, runtime, gateway, path = env
    prepare(client, runtime, A)
    assert deploy(client).status_code == 201
    prepare(client, runtime, B)
    assert request(client, "POST", "/api/v1/local/deployments/prod/promote",
                   {"tag": B}).status_code == 200
    store = LocalDeploymentStore(path)
    original = store.get("prod")
    old_loaded = runtime.loaded
    interleaving = {"performed": False}

    def competing_operator_pause():
        if not interleaving["performed"]:
            interleaving["performed"] = True
            store.set_enabled("prod", False)
        return old_loaded()

    runtime.loaded = competing_operator_pause
    result = request(client, "POST", "/api/v1/local/deployments/prod/rollback")
    assert result.status_code == 409
    assert interleaving["performed"]
    final = store.get("prod")
    assert final["enabled"] is False
    assert final["active_tag"] == B
    assert final["previous_tag"] == A
    assert final["previous_digest"] == DIGEST_A
    assert final["updated_at_utc"] != original["updated_at_utc"]
    assert gateway.calls == []


def test_direct_rollback_rejects_stale_preflight_active_or_updated_state(env):
    client, runtime, gateway, path = env
    prepare(client, runtime, A)
    assert deploy(client).status_code == 201
    prepare(client, runtime, B)
    assert request(client, "POST", "/api/v1/local/deployments/prod/promote",
                   {"tag": B}).status_code == 200
    store = LocalDeploymentStore(path)
    before = store.get("prod")
    changed = store.set_enabled("prod", False)
    assert changed["previous_tag"] == before["previous_tag"]
    assert changed["active_tag"] == before["active_tag"]
    with pytest.raises(Exception) as rejected:
        store.rollback("prod", expected_previous_tag=A,
                       expected_previous_digest=DIGEST_A,
                       expected_current=before)
    assert getattr(rejected.value, "status_code", None) == 409
    assert store.get("prod") == changed
