"""Atomic shared Redis budget for authorized per-project inference attempts.

One atomic Lua decision per successfully authorized attempt, no quota claims
from JWTs/headers, no prompt text or plaintext tenant IDs in Redis keys.
Not a distributed in-flight/concurrency limiter or billing system.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import os
from uuid import UUID

from fastapi import HTTPException

from app.security.redis_quota import local_acl_redis_client

REDIS_ENV = "METRIQEN_PROJECT_QUOTA_REDIS_URL"
SECRET_ENV = "METRIQEN_PROJECT_QUOTA_HMAC_SECRET"
LIMIT_ENV = "METRIQEN_PROJECT_QUOTA_REQUESTS_PER_MINUTE"
DEFAULT_LIMIT = 60
MAX_LIMIT = 1000
WINDOW_SECONDS = 60
KEY_PREFIX = "metriqen:project-inference:v1:"

# GET, bound check, INCR, and first-hit EXPIRE are one Redis transaction.
# A denied attempt does not increment the count or extend the TTL.
# Return 1 only for an admitted request, 0 at the hard ceiling.
PROJECT_QUOTA_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if value ~= false then
    local count = tonumber(value)
    if count == nil then
        return redis.error_reply('invalid counter')
    end
    if count >= tonumber(ARGV[2]) then
        return 0
    end
end
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return 1
"""


class RedisProjectQuota:
    """A request budget shared across every control API and gateway replica."""

    def __init__(self, connection, secret: bytes, *, limit: int = DEFAULT_LIMIT,
                 window_seconds: int = WINDOW_SECONDS):
        if not isinstance(secret, bytes) or not 32 <= len(secret) <= 512:
            raise ValueError("project quota hashing secret must be 32–512 bytes")
        if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
            raise ValueError("project quota limit is invalid")
        if type(window_seconds) is not int or not 1 <= window_seconds <= WINDOW_SECONDS:
            raise ValueError("project quota window is invalid")
        self._connection = connection
        self._secret = secret
        self.limit = limit
        self.window_seconds = window_seconds

    def enforce(self, organization_id: UUID, project_id: UUID) -> None:
        if not isinstance(organization_id, UUID) or not isinstance(project_id, UUID):
            raise HTTPException(503, "project inference quota unavailable")
        # Project IDs are globally unique, but include the authoritative DB
        # organization ID to bind the budget to its resolved tenant.
        digest = hmac.new(
            self._secret, ("project-inference\\x00" + str(organization_id) +
                           "\\x00" + str(project_id)).encode("ascii"), hashlib.sha256,
        ).hexdigest()
        key = KEY_PREFIX + digest
        try:
            result = self._connection.eval(
                PROJECT_QUOTA_SCRIPT, 1, key, self.window_seconds, self.limit,
            )
            if type(result) is not int or result not in (0, 1):
                raise ValueError("unexpected Redis project quota result")
        except Exception:
            raise HTTPException(503, "project inference quota unavailable") from None
        if result == 0:
            raise HTTPException(
                429, "project inference request quota exceeded",
                headers={"Retry-After": str(self.window_seconds)},
            )


def load_project_quota(
    environ: Mapping[str, str] | None = None,
) -> RedisProjectQuota | None:
    """Configured explicitly, independent of the account-mutation quota."""
    env = os.environ if environ is None else environ
    url = env.get(REDIS_ENV)
    secret = env.get(SECRET_ENV)
    raw_limit = env.get(LIMIT_ENV)
    if url is None and secret is None and raw_limit is None:
        return None
    if (not isinstance(url, str) or not url or not isinstance(secret, str)
            or not 32 <= len(secret.encode("utf-8")) <= 512
            or not secret.isprintable()):
        raise ValueError("project inference quota configuration is incomplete or invalid")
    if raw_limit is None:
        limit = DEFAULT_LIMIT
    elif (not isinstance(raw_limit, str) or not raw_limit.isascii()
          or not raw_limit.isdecimal() or raw_limit.startswith("0")):
        raise ValueError("project inference quota limit is invalid")
    else:
        try:
            limit = int(raw_limit)
        except ValueError:
            raise ValueError("project inference quota limit is invalid") from None
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError("project inference quota limit is invalid")
    # Never reveal private Redis ACL credentials in startup exceptions.
    try:
        client = local_acl_redis_client(url)
        return RedisProjectQuota(client, secret.encode("utf-8"), limit=limit)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("project inference quota configuration is invalid") from None
