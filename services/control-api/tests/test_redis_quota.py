"""Deterministic Redis quota tests; no external Redis/real passwords needed."""
import threading

from fastapi import HTTPException
import pytest

from app.security.oidc import VerifiedIdentity
from app.security.redis_quota import (
    RedisAccountQuota, load_account_quota, MAX_HITS, WINDOW_SECONDS,
)


ALICE = VerifiedIdentity("alice", "https://issuer-a.test", "metriqen-control-api")
BOB = VerifiedIdentity("bob", "https://issuer-a.test", "metriqen-control-api")
OTHER_ISSUER = VerifiedIdentity("alice", "https://issuer-b.test", "metriqen-control-api")


class FakeAtomicRedis:
    def __init__(self):
        self.counts = {}
        self.lock = threading.Lock()
        self.keys = []
        self.calls = 0
        self.fail = False

    def eval(self, script, key_count, key, ttl):
        with self.lock:
            self.calls += 1
            if self.fail:
                raise ConnectionError("private redis host and ACL credentials")
            assert "redis.call('INCR'" in script
            assert key_count == 1
            assert ttl == WINDOW_SECONDS
            self.keys.append(key)
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]


def test_no_redis_settings_is_opt_out():
    assert load_account_quota({}) is None


@pytest.mark.parametrize("env", [
    {"METRIQEN_REDIS_URL": "redis://default:very-long-dev-secret@127.0.0.1:6379/0"},
    {"METRIQEN_RATE_LIMIT_HMAC_SECRET": "x" * 32},
    {"METRIQEN_REDIS_URL": "redis://default:very-long-dev-secret@public.example:6379/0",
     "METRIQEN_RATE_LIMIT_HMAC_SECRET": "x" * 32},
    {"METRIQEN_REDIS_URL": "redis://default:very-long-dev-secret@127.0.0.1:6380/0",
     "METRIQEN_RATE_LIMIT_HMAC_SECRET": "x" * 32},
    {"METRIQEN_REDIS_URL": "redis://127.0.0.1:6379/0",
     "METRIQEN_RATE_LIMIT_HMAC_SECRET": "x" * 32},
    {"METRIQEN_REDIS_URL": "redis://default:very-long-dev-secret@127.0.0.1:6379/1",
     "METRIQEN_RATE_LIMIT_HMAC_SECRET": "x" * 32},
    {"METRIQEN_REDIS_URL": "redis://default:very-long-dev-secret@127.0.0.1:6379/0?db=1",
     "METRIQEN_RATE_LIMIT_HMAC_SECRET": "x" * 32},
    {"METRIQEN_REDIS_URL": "redis://default:very-long-dev-secret@127.0.0.1:6379/0",
     "METRIQEN_RATE_LIMIT_HMAC_SECRET": "short"},
])
def test_unsafe_or_partial_configuration_is_rejected_without_leaking_url(env):
    with pytest.raises(ValueError) as info:
        load_account_quota(env)
    assert "very-long-dev-secret" not in str(info.value)
    assert "public.example" not in str(info.value)


def test_valid_local_acl_config_creates_client_without_connecting(monkeypatch):
    import app.security.redis_quota as module
    fake = FakeAtomicRedis()
    observed = []

    def fake_from_url(url, **kwargs):
        observed.append((url, kwargs))
        return fake

    monkeypatch.setattr(module.redis.Redis, "from_url", fake_from_url)
    quota = load_account_quota({
        "METRIQEN_REDIS_URL": "redis://quota_user:very-long-dev-secret@127.0.0.1:6379/0",
        "METRIQEN_RATE_LIMIT_HMAC_SECRET": "x" * 32,
    })
    assert isinstance(quota, RedisAccountQuota)
    assert observed[0][1]["socket_connect_timeout"] == 1
    quota.enforce(ALICE)
    assert fake.calls == 1


def test_global_atomic_limit_rejects_after_twenty_mutations():
    fake = FakeAtomicRedis()
    quota_a = RedisAccountQuota(fake, b"x" * 32)
    quota_b = RedisAccountQuota(fake, b"x" * 32)
    for n in range(MAX_HITS):
        (quota_a if n % 2 else quota_b).enforce(ALICE)
    with pytest.raises(HTTPException) as failure:
        quota_a.enforce(ALICE)
    assert failure.value.status_code == 429
    assert failure.value.headers == {"Retry-After": "60"}
    assert len(set(fake.keys)) == 1
    assert "alice" not in fake.keys[0]
    assert "issuer-a" not in fake.keys[0]


def test_distinct_verified_issuer_and_subject_have_independent_limits():
    fake = FakeAtomicRedis()
    quota = RedisAccountQuota(fake, b"x" * 32)
    quota.enforce(ALICE)
    quota.enforce(BOB)
    quota.enforce(OTHER_ISSUER)
    assert len(set(fake.keys)) == 3


def test_redis_outage_or_malformed_response_fails_closed_without_leak():
    fake = FakeAtomicRedis()
    fake.fail = True
    quota = RedisAccountQuota(fake, b"x" * 32)
    with pytest.raises(HTTPException) as error:
        quota.enforce(ALICE)
    assert error.value.status_code == 503
    assert "private" not in str(error.value.detail)
    fake.fail = False
    fake.eval = lambda *args: "one"  # untrusted/invalid Redis protocol data
    with pytest.raises(HTTPException) as error:
        quota.enforce(ALICE)
    assert error.value.status_code == 503
