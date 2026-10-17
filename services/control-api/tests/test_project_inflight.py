"""Synthetic distributed per-project in-flight lease adversarial contracts."""
from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.security.project_inflight import (
    KEY_PREFIX, LEASE_MS, RedisProjectInflight, load_project_inflight,
)
from app.security.policy import Role
from test_project_access_http import sandbox, signed_material, token, ISSUER, ORG_A, PROJECT_A

SECRET = b"synthetic-test-project-inflight-secret-0123456789abcdef"
SERVICE = "synthetic-private-service-0123456789abcdef0123456789abcdef"
BASE = "/internal/gateway"
URL = "redis://inflight_user:synthetic-long-redis-password@127.0.0.1:6379/0"


class FakeRedis:
    INVALID_SENTINEL = object()

    def __init__(self):
        self.lock = threading.Lock()
        self.members = {}
        self.now = 0
        self.fail = False
        self.override = self.INVALID_SENTINEL
        self.calls = []

    def eval(self, script, keys, key, *args):
        with self.lock:
            if self.fail:
                raise RuntimeError("REDIS_PRIVATE_CREDENTIAL_NEVER_RETURN")
            if self.override is not self.INVALID_SENTINEL:
                return self.override
            self.calls.append((key, args))
            if "ZREMRANGEBYSCORE" in script:
                limit, ttl, permit = args
                assert ttl == LEASE_MS
                live = self.members.setdefault(key, {})
                for member, expiry in list(live.items()):
                    if expiry <= self.now:
                        del live[member]
                if len(live) >= limit:
                    return 0
                assert permit not in live
                live[permit] = self.now + ttl
                return 1
            assert "ZREM" in script
            permit = args[0]
            live = self.members.get(key, {})
            return int(live.pop(permit, None) is not None)


def test_atomic_limit_spans_replicas_under_concurrent_acquire():
    fake = FakeRedis()
    instances = [RedisProjectInflight(fake, SECRET, limit=3),
                 RedisProjectInflight(fake, SECRET, limit=3)]
    org, project, other = uuid4(), uuid4(), uuid4()

    def attempt(i):
        try:
            return instances[i % 2].acquire(org, project)
        except HTTPException as error:
            assert error.status_code == 429
            assert error.headers == {"Retry-After": "1"}
            return None

    with ThreadPoolExecutor(max_workers=24) as pool:
        decisions = list(pool.map(attempt, range(120)))
    granted = [d for d in decisions if d is not None]
    assert len(granted) == 3
    assert len({d["permit"] for d in granted}) == 3
    assert len({d["scope"] for d in granted}) == 1
    assert len(fake.members) == 1
    key = next(iter(fake.members))
    assert key.startswith(KEY_PREFIX)
    assert str(project) not in key and str(org) not in key
    assert all(str(project) not in str(call) for call in fake.calls)
    assert instances[0].release(**granted[0]) is True
    assert instances[1].release(**granted[0]) is False
    fourth = instances[1].acquire(org, project)
    assert fourth["scope"] == granted[0]["scope"]
    assert instances[1].acquire(org, other)["scope"] != fourth["scope"]
    assert instances[1].acquire(uuid4(), project)["scope"] != fourth["scope"]
    fake.now = LEASE_MS + 1
    assert instances[0].acquire(org, project)


def test_bad_redis_result_outage_and_bad_release_fail_closed():
    fake = FakeRedis()
    limiter = RedisProjectInflight(fake, SECRET)
    org, project = uuid4(), uuid4()
    fake.fail = True
    with pytest.raises(HTTPException) as err:
        limiter.acquire(org, project)
    assert err.value.status_code == 503
    assert "PRIVATE" not in str(err.value.detail)
    with pytest.raises(HTTPException) as err:
        limiter.release("a" * 64, "b" * 32)
    assert err.value.status_code == 503
    fake.fail = False
    for result in ("1", True, None, -1, 2, [], b"1"):
        fake.override = result
        with pytest.raises(HTTPException) as err:
            limiter.acquire(org, project)
        assert err.value.status_code == 503
        with pytest.raises(HTTPException) as err:
            limiter.release("a" * 64, "b" * 32)
        assert err.value.status_code == 503
    fake.override = None
    for bad in ("../a", "A" * 64, "a" * 63, "a" * 65, " " * 64):
        with pytest.raises(HTTPException) as err:
            limiter.release(bad, "b" * 32)
        assert err.value.status_code == 422


def test_verified_jwt_project_permissions_before_admission_and_secret_on_release(sandbox):
    _, store, signer, _, settings = sandbox
    fake = FakeRedis()
    quota = RedisProjectInflight(fake, SECRET, limit=1)
    client = TestClient(create_app(
        settings, project_store=store, gateway_internal_token=SERVICE,
        project_inflight=quota,
    ), client=("127.0.0.1", 50100))
    auth = {
        "X-Metriqen-Gateway-Secret": SERVICE,
        "Authorization": "Bearer " + token(signer, subject="user-a", role="owner"),
    }
    def authorize():
        return client.post(BASE + "/authorize",
                           json={"project_id": str(PROJECT_A)}, headers=auth)
    assert authorize().status_code == 404  # Forged JWT role does not count.
    assert fake.calls == []
    store.grant(Role.VIEWER, projects=(PROJECT_A,))
    assert authorize().status_code == 404
    assert fake.calls == []
    store.grant(Role.DEVELOPER, projects=(PROJECT_A,))
    granted = authorize()
    assert granted.status_code == 200, granted.text
    assert granted.headers["Cache-Control"] == "no-store"
    data = granted.json()
    assert data["authorized"] is True
    permit = data["lease"]
    assert set(permit) == {"scope", "permit"}
    assert authorize().status_code == 429
    assert len(fake.members) == 1

    release = lambda headers: client.post(BASE + "/release", json=permit, headers=headers)
    assert release({}).status_code == 403
    assert release({"X-Metriqen-Gateway-Secret": "forged"}).status_code == 403
    remote = TestClient(client.app, client=("192.0.2.11", 50101))
    assert remote.post(BASE + "/release", json=permit,
                       headers={"X-Metriqen-Gateway-Secret": SERVICE}).status_code == 403
    bad = client.post(BASE + "/release",
                     json={**permit, "extra": "forged"},
                     headers={"X-Metriqen-Gateway-Secret": SERVICE})
    assert bad.status_code == 422
    assert release({"X-Metriqen-Gateway-Secret": SERVICE}).status_code == 204
    assert release({"X-Metriqen-Gateway-Secret": SERVICE}).status_code == 204
    assert authorize().status_code == 200
    del store.memberships[(ISSUER, "user-a", ORG_A)]
    assert authorize().status_code == 404  # Same bearer is revoked.
    assert len(fake.members) == 1


@pytest.mark.parametrize("env", [
    {"METRIQEN_PROJECT_INFLIGHT_MAX_ACTIVE": "2"},
    {"METRIQEN_PROJECT_INFLIGHT_REDIS_URL": URL},
    {"METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET": "x" * 32},
    {"METRIQEN_PROJECT_INFLIGHT_REDIS_URL": URL,
     "METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET": "short"},
    {"METRIQEN_PROJECT_INFLIGHT_REDIS_URL": URL,
     "METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET": "x" * 32,
     "METRIQEN_PROJECT_INFLIGHT_MAX_ACTIVE": "0"},
    {"METRIQEN_PROJECT_INFLIGHT_REDIS_URL": URL,
     "METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET": "x" * 32,
     "METRIQEN_PROJECT_INFLIGHT_MAX_ACTIVE": "017"},
    {"METRIQEN_PROJECT_INFLIGHT_REDIS_URL": URL,
     "METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET": "x" * 32,
     "METRIQEN_PROJECT_INFLIGHT_MAX_ACTIVE": "17"},
    {"METRIQEN_PROJECT_INFLIGHT_REDIS_URL": "redis://evil.example:6379/0",
     "METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET": "x" * 32},
])
def test_invalid_or_partial_configuration_rejected(env):
    with pytest.raises(ValueError) as err:
        load_project_inflight(env)
    assert "synthetic-long-redis-password" not in str(err.value)


def test_explicit_opt_in_no_eager_network_and_trusted_project_config(sandbox, monkeypatch):
    from app.security import redis_quota
    _, store, _, _, settings = sandbox
    fake = FakeRedis()
    monkeypatch.setattr(redis_quota.redis.Redis, "from_url", lambda *args, **kwargs: fake)
    configured = load_project_inflight({
        "METRIQEN_PROJECT_INFLIGHT_REDIS_URL": URL,
        "METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET": "x" * 32,
        "METRIQEN_PROJECT_INFLIGHT_MAX_ACTIVE": "4",
    })
    assert configured.limit == 4
    assert load_project_inflight({}) is None
    with pytest.raises(ValueError):
        create_app(settings, project_inflight=configured)
    with pytest.raises(ValueError):
        create_app(settings, project_store=store, project_inflight=configured)
    with pytest.raises(ValueError):
        create_app(project_store=store, gateway_internal_token=SERVICE,
                   project_inflight=configured)
