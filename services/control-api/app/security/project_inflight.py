"""Optional shared Redis active-inference leases for guarded local gateways.

Atomic Redis TIME+ZSET admission across API/gateway replicas; bounded expiry
recovers crashed gateways. The gateway MUST release in a finally equivalent.
These permits do not authenticate a user or act as live serving capabilities.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import os
import secrets
from uuid import UUID

from fastapi import HTTPException
from app.security.redis_quota import local_acl_redis_client

URL_ENV = "METRIQEN_PROJECT_INFLIGHT_REDIS_URL"
SECRET_ENV = "METRIQEN_PROJECT_INFLIGHT_HMAC_SECRET"
LIMIT_ENV = "METRIQEN_PROJECT_INFLIGHT_MAX_ACTIVE"
KEY_PREFIX = "metriqen:project-inflight:v1:"
LEASE_MS = 240000
MAX_ACTIVE = 16
DEFAULT_ACTIVE = 2

# Redis TIME avoids inconsistent clocks between control API replicas. ZSET
# mutations and bound checks are one EVAL, not GET followed by ZADD. No raw
# organization, subject, project, bearer, prompt or model data enters Redis.
ACQUIRE_SCRIPT = """
local now = redis.call('TIME')
local ms = tonumber(now[1])*1000 + math.floor(tonumber(now[2])/1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ms)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
    return 0
end
redis.call('ZADD', KEYS[1], ms + tonumber(ARGV[2]), ARGV[3])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) + 1000)
return 1
"""
RELEASE_SCRIPT = "return redis.call('ZREM', KEYS[1], ARGV[1])"


class RedisProjectInflight:
    def __init__(self, connection, secret: bytes, *, limit: int = DEFAULT_ACTIVE):
        if not isinstance(secret, bytes) or not 32 <= len(secret) <= 512:
            raise ValueError("invalid project inflight HMAC secret")
        if type(limit) is not int or not 1 <= limit <= MAX_ACTIVE:
            raise ValueError("invalid project inflight active limit")
        self._connection = connection
        self._secret = secret
        self.limit = limit

    def acquire(self, organization_id: UUID, project_id: UUID) -> dict[str, str]:
        if not isinstance(organization_id, UUID) or not isinstance(project_id, UUID):
            raise HTTPException(503, "project active inference limit unavailable")
        scope = hmac.new(
            self._secret,
            ("inflight\x00" + str(organization_id) + "\x00" + str(project_id)).encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        permit = secrets.token_hex(16)
        try:
            result = self._connection.eval(
                ACQUIRE_SCRIPT, 1, KEY_PREFIX + scope, self.limit, LEASE_MS, permit,
            )
            if type(result) is not int or result not in (0, 1):
                raise ValueError("unexpected Redis active admission result")
        except Exception:
            raise HTTPException(503, "project active inference limit unavailable") from None
        if result == 0:
            raise HTTPException(
                429, "project active inference capacity exceeded",
                headers={"Retry-After": "1"},
            )
        return {"scope": scope, "permit": permit}

    def release(self, scope: str, permit: str) -> bool:
        # Strict upper bounds and syntax before touching the shared Redis ACL.
        if (not isinstance(scope, str) or len(scope) != 64 or
                not all(c in "0123456789abcdef" for c in scope) or
                not isinstance(permit, str) or len(permit) != 32 or
                not all(c in "0123456789abcdef" for c in permit)):
            raise HTTPException(422, "invalid project inference permit")
        try:
            result = self._connection.eval(RELEASE_SCRIPT, 1, KEY_PREFIX + scope, permit)
            if type(result) is not int or result not in (0, 1):
                raise ValueError("unexpected Redis active release result")
        except Exception:
            raise HTTPException(503, "project active inference limit unavailable") from None
        return result == 1


def load_project_inflight(environ: Mapping[str, str] | None = None) -> RedisProjectInflight | None:
    env = os.environ if environ is None else environ
    url, secret, raw_limit = (env.get(URL_ENV), env.get(SECRET_ENV),
                              env.get(LIMIT_ENV))
    if url is None and secret is None and raw_limit is None:
        return None
    if (not isinstance(url, str) or not url or not isinstance(secret, str) or
            not secret.isprintable() or not 32 <= len(secret.encode("utf-8")) <= 512):
        raise ValueError("project active inference configuration incomplete or invalid")
    if raw_limit is None:
        limit = DEFAULT_ACTIVE
    elif (not isinstance(raw_limit, str) or not raw_limit.isascii() or
          not raw_limit.isdecimal() or raw_limit.startswith("0")):
        raise ValueError("invalid project active inference limit")
    else:
        try:
            limit = int(raw_limit)
        except ValueError:
            raise ValueError("invalid project active inference limit") from None
    if not 1 <= limit <= MAX_ACTIVE:
        raise ValueError("invalid project active inference limit")
    try:
        return RedisProjectInflight(local_acl_redis_client(url),
                                    secret.encode("utf-8"), limit=limit)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("project active inference configuration invalid") from None
