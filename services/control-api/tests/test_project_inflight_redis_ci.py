"""Real ephemeral Redis 7 Lua + ACL integration for in-flight permits.

Requires the existing GitHub Actions loopback Redis CI service explicitly
enabled by METRIQEN_REDIS_CI=1; never touches production Redis.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

from fastapi import HTTPException
import pytest
import redis

from app.security.project_inflight import (
    KEY_PREFIX, LEASE_MS, RedisProjectInflight, load_project_inflight,
)
from app.security.project_output_reservations import (
    KEY_PREFIX as TOKEN_KEYS, RedisProjectOutputReservations,
    load_project_output_reservations,
)

pytestmark = pytest.mark.skipif(
    os.getenv("METRIQEN_REDIS_CI") != "1",
    reason="requires disposable loopback Redis 7 with privileged synthetic ACL setup",
)

PASSWORD = "synthetic-only-project-inflight-redis-password"
USER = "project_inflight_ci"
URL = f"redis://{USER}:{PASSWORD}@127.0.0.1:6379/0"
SECRET = b"ci-verified-project-inflight-scope-secret-0123456789abcdef"


@pytest.fixture
def clients():
    admin = redis.Redis(host="127.0.0.1", port=6379, db=0, socket_timeout=2)
    admin.ping()
    admin.execute_command(
        "ACL", "SETUSER", USER, "on", ">" + PASSWORD,
        "~" + KEY_PREFIX + "*", "~" + TOKEN_KEYS + "*",
        "+eval", "+evalsha", "+time", "+zremrangebyscore",
        "+zcard", "+zadd", "+pexpire", "+zrem", "+ping",
        "+zscore", "+exists", "+get", "+incrby", "+expire", "+set",
    )
    a = redis.Redis.from_url(URL, socket_timeout=2, socket_connect_timeout=1)
    b = redis.Redis.from_url(URL, socket_timeout=2, socket_connect_timeout=1)
    try:
        a.ping()
        b.ping()
        yield admin, a, b
    finally:
        for prefix in (KEY_PREFIX, TOKEN_KEYS):
            for key in admin.scan_iter(match=prefix + "*"):
                admin.delete(key)
        a.close()
        b.close()
        admin.execute_command("ACL", "DELUSER", USER)
        admin.close()


def test_atomic_redis_concurrency_limit_across_independent_clients(clients):
    admin, a, b = clients
    stores = [RedisProjectInflight(a, SECRET, limit=4),
              RedisProjectInflight(b, SECRET, limit=4)]
    org, project = uuid4(), uuid4()

    def invoke(i):
        try:
            return stores[i % 2].acquire(org, project)
        except HTTPException as exc:
            assert exc.status_code == 429
            assert exc.headers == {"Retry-After": "1"}
            return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(invoke, range(100)))
    admitted = [r for r in results if r is not None]
    assert len(admitted) == 4
    assert len({d["scope"] for d in admitted}) == 1
    assert len({d["permit"] for d in admitted}) == 4
    keys = list(admin.scan_iter(match=KEY_PREFIX + "*"))
    assert len(keys) == 1
    assert str(project).encode() not in keys[0]
    assert str(org).encode() not in keys[0]
    assert int(admin.zcard(keys[0])) == 4
    ttl = admin.pttl(keys[0])
    assert 0 < ttl <= LEASE_MS + 1000

    assert stores[0].release(**admitted[0]) is True
    assert stores[1].release(**admitted[0]) is False
    replacement = stores[1].acquire(org, project)
    assert replacement["scope"] == admitted[0]["scope"]
    assert replacement["permit"] != admitted[0]["permit"]
    assert admin.zcard(keys[0]) == 4

    # Crash scenario: a gateway disappears without releasing its permit.
    # The server uses Redis TIME; simulate a previously expired claim by
    # replacing its score, then verify the next admission atomically prunes.
    now = admin.time()
    timestamp = now[0] * 1000 + now[1] // 1000
    admin.zadd(keys[0], {admitted[1]["permit"]: timestamp - 1})
    recovered = stores[0].acquire(org, project)
    assert recovered["permit"] not in {p["permit"] for p in admitted}
    assert admin.zcard(keys[0]) == 4

    # Same project name alone cannot forge a separate organization budget.
    unrelated = stores[0].acquire(uuid4(), project)
    assert unrelated["scope"] != replacement["scope"]
    assert admin.zcard(KEY_PREFIX + unrelated["scope"]) == 1


def test_scoped_acl_config_and_disallowed_namespace(clients):
    _, a, b = clients
    loaded = load_project_inflight({
        "METRIQEN_PROJECT_INFLIGHT_REDIS_URL": URL,
        "METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET": SECRET.decode(),
        "METRIQEN_PROJECT_INFLIGHT_MAX_ACTIVE": "1",
    })
    assert loaded is not None and loaded.limit == 1
    lease = loaded.acquire(uuid4(), uuid4())
    assert loaded.release(**lease)
    assert not loaded.release(**lease)
    loaded._connection.close()
    with pytest.raises(redis.exceptions.NoPermissionError):
        a.get("metriqen:project-inference:v1:should-be-private")
    with pytest.raises(redis.exceptions.NoPermissionError):
        b.zcard("other-app:tenant-key")


def test_real_redis_output_cap_reservations_atomic_across_control_replicas(clients):
    admin, a, b = clients
    org, project = uuid4(), uuid4()
    lease_stores = [RedisProjectInflight(a, SECRET, limit=16),
                    RedisProjectInflight(b, SECRET, limit=16)]
    budgets = [RedisProjectOutputReservations(lease_stores[0], limit=1024),
               RedisProjectOutputReservations(lease_stores[1], limit=1024)]
    permits = [lease_stores[i % 2].acquire(org, project) for i in range(16)]

    def reserve(i):
        try:
            budgets[i % 2].reserve(**permits[i], requested_max_tokens=256)
            return 204
        except HTTPException as exc:
            assert exc.status_code == 429
            assert exc.headers == {"Retry-After": "60"}
            return 429

    with ThreadPoolExecutor(max_workers=16) as pool:
        responses = list(pool.map(reserve, range(16)))
    assert responses.count(204) == 4
    assert responses.count(429) == 12
    scope = permits[0]["scope"]
    budget_key = TOKEN_KEYS + "budget:" + scope
    assert admin.get(budget_key) == b"1024"
    assert 0 < admin.ttl(budget_key) <= 60
    assert len(list(admin.scan_iter(match=TOKEN_KEYS + "receipt:*"))) == 4

    admitted = next(i for i, code in enumerate(responses) if code == 204)
    with pytest.raises(HTTPException) as replay:
        budgets[1].reserve(**permits[admitted], requested_max_tokens=1)
    assert replay.value.status_code == 409
    assert admin.get(budget_key) == b"1024"
    lease_stores[0].release(**permits[admitted])
    assert admin.get(budget_key) == b"1024"  # no refunds
    with pytest.raises(HTTPException) as after_release:
        budgets[0].reserve(**permits[admitted], requested_max_tokens=1)
    assert after_release.value.status_code == 409
    outsider = lease_stores[0].acquire(uuid4(), project)
    budgets[0].reserve(**outsider, requested_max_tokens=512)
    assert outsider["scope"] != scope
    assert admin.get(budget_key) == b"1024"
    for key in admin.scan_iter(match=TOKEN_KEYS + "*"):
        assert str(org).encode() not in key and str(project).encode() not in key


def test_real_redis_expired_permit_and_window_reset_without_refund(clients):
    admin, a, b = clients
    store = RedisProjectInflight(a, SECRET, limit=3)
    budget = RedisProjectOutputReservations(store, limit=256)
    org, project = uuid4(), uuid4()
    first = store.acquire(org, project)
    budget.reserve(**first, requested_max_tokens=256)
    budget_key = TOKEN_KEYS + "budget:" + first["scope"]
    assert admin.get(budget_key) == b"256"
    with pytest.raises(HTTPException) as exhausted:
        budget.reserve(**store.acquire(org, project), requested_max_tokens=256)
    assert exhausted.value.status_code == 429
    # A valid request cannot be charged twice, even after window expiry.
    admin.pexpire(budget_key, 1)
    import time
    time.sleep(0.04)
    with pytest.raises(HTTPException) as replay:
        budget.reserve(**first, requested_max_tokens=256)
    assert replay.value.status_code == 409
    second = store.acquire(org, project)
    budget.reserve(**second, requested_max_tokens=256)
    assert admin.get(budget_key) == b"256"
    # Redis TIME proves an expired lease cannot spend: simulate a failed
    # gateway permit whose TTL has elapsed before reaching reserve.
    active_key = KEY_PREFIX + second["scope"]
    now = admin.time()
    admin.zadd(active_key, {second["permit"]: now[0]*1000 + now[1]//1000 - 1})
    with pytest.raises(HTTPException) as expired:
        budget.reserve(**second, requested_max_tokens=1)
    assert expired.value.status_code == 409
    assert admin.get(budget_key) == b"256"


def test_redis_scoped_output_budget_configuration_and_acl(clients):
    _, a, b = clients
    store = load_project_inflight({
        "METRIQEN_PROJECT_INFLIGHT_REDIS_URL": URL,
        "METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET": SECRET.decode(),
        "METRIQEN_PROJECT_INFLIGHT_MAX_ACTIVE": "2",
    })
    budget = load_project_output_reservations(store, {
        "METRIQEN_PROJECT_OUTPUT_RESERVATION_PER_MINUTE": "1024",
    })
    lease = store.acquire(uuid4(), uuid4())
    budget.reserve(**lease, requested_max_tokens=256)
    assert budget.limit == 1024
    with pytest.raises(redis.exceptions.NoPermissionError):
        a.get("metriqen:project-inference:v1:forbidden-tenant")
    with pytest.raises(redis.exceptions.NoPermissionError):
        b.get("unrelated:billing-data")
    store._connection.close()
