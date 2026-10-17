"""Actual Redis 7 Lua/ACL concurrency contract; CI-only ephemeral service.

No real production Redis or external IdP is involved. Test uses synthetic
credentials against Redis running on GitHub Actions loopback only.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import time
from uuid import uuid4

from fastapi import HTTPException
import pytest
import redis

from app.security.project_quota import (
    KEY_PREFIX, RedisProjectQuota, load_project_quota,
)

pytestmark = pytest.mark.skipif(
    os.getenv("METRIQEN_REDIS_CI") != "1",
    reason="requires explicitly provisioned disposable Redis 7 CI service",
)

PASSWORD = "synthetic-ci-project-quota-password"
ACL_USER = "project_quota_ci"
URL = f"redis://{ACL_USER}:{PASSWORD}@127.0.0.1:6379/0"
SECRET = b"ci-project-quota-signing-secret-0123456789abcdef"


@pytest.fixture
def redis_clients():
    admin = redis.Redis(host="127.0.0.1", port=6379, db=0, socket_timeout=2)
    admin.ping()
    # This user is restricted to the inference-quota key namespace.
    admin.execute_command(
        "ACL", "SETUSER", ACL_USER, "on", ">" + PASSWORD,
        "~" + KEY_PREFIX + "*",
        "+eval", "+evalsha", "+get", "+incr", "+expire", "+ping",
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
        admin.execute_command("ACL", "DELUSER", ACL_USER)
        admin.close()


def test_real_redis_concurrent_replicas_share_one_atomic_budget(redis_clients):
    admin, a, b = redis_clients
    quotas = [RedisProjectQuota(a, SECRET, limit=17),
              RedisProjectQuota(b, SECRET, limit=17)]
    org, project = uuid4(), uuid4()

    def admit(n):
        try:
            quotas[n % 2].enforce(org, project)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(admit, range(140)))
    assert results.count(200) == 17
    assert results.count(429) == 123
    keys = list(admin.scan_iter(match=KEY_PREFIX + "*"))
    assert len(keys) == 1
    assert str(project).encode() not in keys[0]
    assert str(org).encode() not in keys[0]
    assert int(admin.get(keys[0])) == 17
    first_ttl = admin.ttl(keys[0])
    assert 0 < first_ttl <= 60
    with pytest.raises(HTTPException) as denied:
        quotas[1].enforce(org, project)
    assert denied.value.status_code == 429
    assert int(admin.get(keys[0])) == 17
    assert 0 < admin.ttl(keys[0]) <= first_ttl


def test_real_redis_acl_client_and_fixed_window_expiry(redis_clients):
    admin, a, b = redis_clients
    # Load through the exact production local-ACL validation path.
    env = {
        "METRIQEN_PROJECT_QUOTA_REDIS_URL": URL,
        "METRIQEN_PROJECT_QUOTA_HMAC_SECRET": SECRET.decode(),
        "METRIQEN_PROJECT_QUOTA_REQUESTS_PER_MINUTE": "2",
    }
    configured = load_project_quota(env)
    assert configured is not None and configured.limit == 2
    c = configured._connection
    try:
        c.ping()
        project, org = uuid4(), uuid4()
        fast = RedisProjectQuota(c, SECRET, limit=2, window_seconds=2)
        fast.enforce(org, project)
        fast.enforce(org, project)
        with pytest.raises(HTTPException) as exhausted:
            fast.enforce(org, project)
        assert exhausted.value.status_code == 429
        assert exhausted.value.headers == {"Retry-After": "2"}
        time.sleep(2.15)
        fast.enforce(org, project)
        with pytest.raises(redis.exceptions.NoPermissionError):
            c.get("other-app:cannot-read")
    finally:
        c.close()
