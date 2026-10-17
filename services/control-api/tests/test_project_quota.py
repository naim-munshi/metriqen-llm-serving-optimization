"""Adversarial atomic per-project Redis quota and control API tests.

The fake executes one Lua-equivalent transaction under a shared lock; the
separate CI Redis suite executes the real checked-in Lua against Redis 7.
"""
from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.security.project_quota import (
    DEFAULT_LIMIT, KEY_PREFIX, RedisProjectQuota, load_project_quota,
)
from app.security.policy import Role
from test_project_access_http import (
    sandbox, signed_material, ISSUER, ORG_A, ORG_B, PROJECT_A, PROJECT_A2,
    PROJECT_B, token,
)

SECRET = b"synthetic-project-quota-key-material-0123456789abcdef"
AUTH_SECRET = "synthetic-service-0123456789abcdef-0123456789abcdef"
REDIS_URL = "redis://quota_user:only-ci-synthetic-password@127.0.0.1:6379/0"
ENDPOINT = "/internal/gateway/authorize"


class AtomicFakeRedis:
    INVALID_SENTINEL = object()

    def __init__(self):
        self.lock = threading.Lock()
        self.counts = {}
        self.expiry = {}
        self.now = 0
        self.keys = []
        self.fail = False
        self.override = self.INVALID_SENTINEL

    def eval(self, script, num_keys, key, window, limit):
        with self.lock:
            if self.fail:
                raise ConnectionError("PRIVATE-REDIS-ACL-PASSWORD")
            if self.override is not self.INVALID_SENTINEL:
                return self.override
            assert "redis.call('INCR'" in script
            assert "redis.call('EXPIRE'" in script
            assert "redis.call('GET'" in script
            assert num_keys == 1
            self.keys.append(key)
            if self.expiry.get(key, -1) <= self.now:
                self.counts.pop(key, None)
            used = self.counts.get(key, 0)
            if used >= limit:
                return 0
            self.counts[key] = used + 1
            if used == 0:
                self.expiry[key] = self.now + window
            return 1


def test_exact_global_limit_across_replica_instances_under_race():
    redis = AtomicFakeRedis()
    quotas = [RedisProjectQuota(redis, SECRET, limit=25),
              RedisProjectQuota(redis, SECRET, limit=25)]
    project = uuid4()
    org = uuid4()

    def invoke(n):
        try:
            quotas[n % 2].enforce(org, project)
            return 200
        except HTTPException as exc:
            assert exc.status_code == 429
            assert exc.headers == {"Retry-After": "60"}
            return exc.status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(invoke, range(150)))
    assert results.count(200) == 25
    assert results.count(429) == 125
    assert len(set(redis.keys)) == 1
    key = redis.keys[0]
    assert key.startswith(KEY_PREFIX)
    assert str(org) not in key and str(project) not in key
    assert redis.counts[key] == 25  # Denied attempts cannot increment.
    assert redis.expiry[key] == 60


def test_independent_projects_do_not_reset_another_budget_or_extend_expiry():
    redis = AtomicFakeRedis()
    quota = RedisProjectQuota(redis, SECRET, limit=2)
    org, project, other = uuid4(), uuid4(), uuid4()
    quota.enforce(org, project)
    quota.enforce(org, project)
    quota.enforce(org, other)
    quota.enforce(uuid4(), project)
    assert len(set(redis.keys)) == 3
    primary = redis.keys[0]
    redis.now = 30
    with pytest.raises(HTTPException) as error:
        quota.enforce(org, project)
    assert error.value.status_code == 429
    assert redis.expiry[primary] == 60
    redis.now = 60
    quota.enforce(org, project)  # First-hit fixed window reset.
    assert redis.expiry[primary] == 120
    assert redis.counts[primary] == 1


def test_outage_and_untrusted_redis_result_fail_closed():
    redis = AtomicFakeRedis()
    quota = RedisProjectQuota(redis, SECRET, limit=2)
    redis.fail = True
    with pytest.raises(HTTPException) as failure:
        quota.enforce(uuid4(), uuid4())
    assert failure.value.status_code == 503
    assert "PRIVATE" not in failure.value.detail
    redis.fail = False
    for item in (True, "1", b"1", -1, 2, [], None):
        redis.override = item
        with pytest.raises(HTTPException) as failure:
            quota.enforce(uuid4(), uuid4())
        assert failure.value.status_code == 503
    with pytest.raises(HTTPException) as failure:
        quota.enforce("forged-org", uuid4())
    assert failure.value.status_code == 503


def test_quota_is_after_verified_permission_and_not_controlled_by_jwt_claims(sandbox):
    _, store, signing, _, settings = sandbox
    redis = AtomicFakeRedis()
    quota = RedisProjectQuota(redis, SECRET, limit=2)
    client = TestClient(
        create_app(settings, project_store=store,
                   gateway_internal_token=AUTH_SECRET, project_quota=quota),
        client=("127.0.0.1", 51230),
    )
    auth = {
        "Authorization": "Bearer " + token(
            signing, subject="user-a", role="owner",
            organization_id=str(ORG_A),
            project_ids=[str(PROJECT_A), str(PROJECT_A2)],
            permissions=["inference.invoke"],
        ),
        "X-Metriqen-Gateway-Secret": AUTH_SECRET,
    }
    def request(project=PROJECT_A, headers=None):
        return client.post(ENDPOINT, json={"project_id": str(project)},
                           headers=auth if headers is None else headers)

    assert request().status_code == 404   # Forged claims never grant usage.
    assert redis.keys == []
    store.grant(Role.VIEWER, projects=(PROJECT_A,))
    assert request().status_code == 404   # project.read != inference.invoke.
    assert redis.keys == []
    store.grant(Role.DEVELOPER, projects=(PROJECT_A,))
    assert request(PROJECT_A2).status_code == 404
    assert request(PROJECT_B).status_code == 404
    assert redis.keys == []
    assert request().status_code == 200
    assert request().status_code == 200
    rejected = request()
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"] == "60"
    assert rejected.headers["Cache-Control"] == "no-store"
    assert len(set(redis.keys)) == 1
    # Identity verification precedes quota, with no project key consumption.
    assert request(headers={
        **auth, "Authorization": "Bearer invalid.jwt",
    }).status_code == 401
    assert request(headers={
        **auth, "X-Metriqen-Gateway-Secret": "untrusted",
    }).status_code == 403
    assert len(redis.keys) == 3
    # The exact same signed token must lose membership access on next request.
    del store.memberships[(ISSUER, "user-a", ORG_A)]
    assert request().status_code == 404
    assert len(redis.keys) == 3
    redis.fail = True
    store.grant(Role.DEVELOPER, projects=(PROJECT_A,))
    assert request().status_code == 503


def test_quota_is_global_to_one_project_across_identities(sandbox):
    _, store, signing, _, settings = sandbox
    redis = AtomicFakeRedis()
    quota = RedisProjectQuota(redis, SECRET, limit=2)
    client = TestClient(create_app(
        settings, project_store=store,
        gateway_internal_token=AUTH_SECRET, project_quota=quota,
    ), client=("127.0.0.1", 51231))
    store.grant(Role.DEVELOPER, subject="user-a", projects=(PROJECT_A,))
    store.grant(Role.ML_ENGINEER, subject="user-b", projects=(PROJECT_A,))
    for user in ("user-a", "user-b"):
        response = client.post(ENDPOINT, json={"project_id": str(PROJECT_A)},
                               headers={
                                   "Authorization": "Bearer " + token(signing, subject=user),
                                   "X-Metriqen-Gateway-Secret": AUTH_SECRET,
                               })
        assert response.status_code == 200
    response = client.post(ENDPOINT, json={"project_id": str(PROJECT_A)}, headers={
        "Authorization": "Bearer " + token(signing, subject="user-b"),
        "X-Metriqen-Gateway-Secret": AUTH_SECRET,
    })
    assert response.status_code == 429
    assert len(set(redis.keys)) == 1


@pytest.mark.parametrize("settings", [
    {"METRIQEN_PROJECT_QUOTA_HMAC_SECRET": "x" * 40},
    {"METRIQEN_PROJECT_QUOTA_REDIS_URL": REDIS_URL},
    {"METRIQEN_PROJECT_QUOTA_REQUESTS_PER_MINUTE": "5"},
    {"METRIQEN_PROJECT_QUOTA_REDIS_URL": REDIS_URL,
     "METRIQEN_PROJECT_QUOTA_HMAC_SECRET": "short"},
    {"METRIQEN_PROJECT_QUOTA_REDIS_URL": REDIS_URL,
     "METRIQEN_PROJECT_QUOTA_HMAC_SECRET": "x" * 32,
     "METRIQEN_PROJECT_QUOTA_REQUESTS_PER_MINUTE": "0"},
    {"METRIQEN_PROJECT_QUOTA_REDIS_URL": REDIS_URL,
     "METRIQEN_PROJECT_QUOTA_HMAC_SECRET": "x" * 32,
     "METRIQEN_PROJECT_QUOTA_REQUESTS_PER_MINUTE": "1001"},
    {"METRIQEN_PROJECT_QUOTA_REDIS_URL": REDIS_URL,
     "METRIQEN_PROJECT_QUOTA_HMAC_SECRET": "x" * 32,
     "METRIQEN_PROJECT_QUOTA_REQUESTS_PER_MINUTE": "01"},
    {"METRIQEN_PROJECT_QUOTA_REDIS_URL": REDIS_URL,
     "METRIQEN_PROJECT_QUOTA_HMAC_SECRET": "x" * 32,
     "METRIQEN_PROJECT_QUOTA_REQUESTS_PER_MINUTE": "+2"},
    {"METRIQEN_PROJECT_QUOTA_REDIS_URL": "redis://quota:pass@public.example:6379/0",
     "METRIQEN_PROJECT_QUOTA_HMAC_SECRET": "x" * 32},
    {"METRIQEN_PROJECT_QUOTA_REDIS_URL": "redis://quota_user:very-long-private-password@127.0.0.1:6379/1",
     "METRIQEN_PROJECT_QUOTA_HMAC_SECRET": "x" * 32},
])
def test_untrusted_or_partial_quota_config_fail_startup(settings):
    with pytest.raises(ValueError) as failure:
        load_project_quota(settings)
    assert "private-password" not in str(failure.value)
    assert "public.example" not in str(failure.value)


def test_valid_config_does_not_connect_eagerly_and_requires_gateway(sandbox, monkeypatch):
    from app.security import redis_quota as module
    _, store, _, _, settings = sandbox
    redis = AtomicFakeRedis()
    observed = []
    monkeypatch.setattr(
        module.redis.Redis, "from_url",
        lambda url, **kwargs: (observed.append((url, kwargs)), redis)[1],
    )
    loaded = load_project_quota({
        "METRIQEN_PROJECT_QUOTA_REDIS_URL": REDIS_URL,
        "METRIQEN_PROJECT_QUOTA_HMAC_SECRET": "x" * 32,
        "METRIQEN_PROJECT_QUOTA_REQUESTS_PER_MINUTE": "2",
    })
    assert isinstance(loaded, RedisProjectQuota)
    assert loaded.limit == 2
    assert observed[0][1]["socket_connect_timeout"] == 1
    assert load_project_quota({}) is None
    with pytest.raises(ValueError):
        create_app(settings, project_store=store, project_quota=loaded)
    with pytest.raises(ValueError):
        create_app(settings, gateway_internal_token=AUTH_SECRET,
                   project_quota=loaded)
    with pytest.raises(ValueError):
        create_app(project_store=store, gateway_internal_token=AUTH_SECRET,
                   project_quota=loaded)
