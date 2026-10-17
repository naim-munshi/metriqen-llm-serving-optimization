"""Opt-in Redis atomic mutation quotas, independent of user authorization.

Default installation has NO Redis dependency configuration. When enabled, a
Redis outage fails account mutations closed, rather than silently skipping
the globally shared quota. Redis never decides access permissions.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import os
import re
from urllib.parse import urlsplit

from fastapi import HTTPException
import redis

from app.security.oidc import VerifiedIdentity

REDIS_ENV = "METRIQEN_REDIS_URL"
SECRET_ENV = "METRIQEN_RATE_LIMIT_HMAC_SECRET"
MAX_HITS = 20
WINDOW_SECONDS = 60

# Lua gives an atomic first-hit 60s TTL and monotonic hit counter, shared by
# all configured control-api replicas. A client-side GET+INCR is NOT atomic.
INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return count
"""


class RedisAccountQuota:
    def __init__(self, connection, secret: bytes):
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("Redis quota hashing secret must be at least 32 bytes")
        self._conn = connection
        self._secret = secret

    def enforce(self, identity: VerifiedIdentity) -> None:
        # Never use raw issuer/subject in Redis keys. A keyed HMAC prevents
        # low-entropy subjects being recovered from a public SHA-256 digest.
        digest = hmac.new(
            self._secret,
            (identity.issuer + "\x00" + identity.subject).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        key = "metriqen:account-writes:v1:" + digest
        try:
            count = self._conn.eval(INCREMENT_SCRIPT, 1, key, WINDOW_SECONDS)
            if type(count) is not int or count < 1:
                raise ValueError("invalid Redis counter result")
        except Exception:
            raise HTTPException(503, "shared account mutation quota unavailable") from None
        if count > MAX_HITS:
            raise HTTPException(429, "account mutation rate limit exceeded",
                                headers={"Retry-After": str(WINDOW_SECONDS)})


def local_acl_redis_client(url: str):
    """Validate a private ACL Redis endpoint without making a network call.

    Used by independent account and project quotas. All connections remain
    loopback-only and authenticate with a dedicated ACL identity.
    """
    if (not isinstance(url, str) or len(url) > 2048 or
            any(ch in url for ch in "\r\n\t")):
        raise ValueError("invalid local Redis configuration")
    parsed = urlsplit(url)
    if (parsed.scheme != "redis" or parsed.hostname != "127.0.0.1"
            or parsed.port != 6379 or parsed.path != "/0"
            or not parsed.username or not parsed.password
            or len(parsed.password) < 16 or parsed.query or parsed.fragment):
        raise ValueError("invalid local Redis configuration")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", parsed.username):
        raise ValueError("invalid local Redis ACL username")
    return redis.Redis.from_url(
        url, decode_responses=False,
        socket_connect_timeout=1, socket_timeout=2,
        socket_keepalive=True, retry_on_timeout=False,
    )


def load_account_quota(
    environ: Mapping[str, str] | None = None,
) -> RedisAccountQuota | None:
    env = os.environ if environ is None else environ
    url = env.get(REDIS_ENV, "")
    secret = env.get(SECRET_ENV, "")
    if not url and not secret:
        return None
    if not url or not secret:
        raise ValueError("Redis quota configuration is incomplete")
    if (not isinstance(url, str) or len(url) > 2048
            or any(ch in url for ch in "\r\n\t")
            or not isinstance(secret, str) or len(secret.encode("utf-8")) < 32
            or len(secret.encode("utf-8")) > 512):
        raise ValueError("Redis quota configuration is invalid")
    try:
        connection = local_acl_redis_client(url)
        return RedisAccountQuota(connection, secret.encode("utf-8"))
    except (ValueError, TypeError, OverflowError):
        # Never leak a Redis password in startup errors.
        raise ValueError("Redis quota configuration is invalid") from None
