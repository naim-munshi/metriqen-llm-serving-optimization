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
        "~" + KEY_PREFIX + "*",
        "+eval", "+evalsha", "+time", "+zremrangebyscore",
        "+zcard", "+zadd", "+pexpire", "+zrem", "+ping",
    )
    a = redis.Redis.from_url(URL, socket_timeout=2, socket_connect_timeout=1)
    b = redis.Redis.from_url(URL, socket_timeout=2, socket_connect_timeout=1)
    try:
        a.ping()
        b.ping()
        yield admin, a, b
    finally:
        for key in admin.scan_iter(match=KEY_PREFIX + "*"):
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
