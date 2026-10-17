"""Optional conservative per-project requested *output-cap* reservations.

This is NOT generated token usage, tokenizer accounting, or customer billing.
A request atomically spends its validated max_tokens before reaching Ollama.
Never refund for client cancellation/model error; fail closed if Redis fails.
One authenticated in-flight lease permits at most one reservation.
"""
from __future__ import annotations

from collections.abc import Mapping
import os
from fastapi import HTTPException

from app.security.project_inflight import (
    RedisProjectInflight, KEY_PREFIX as ACTIVE_KEY_PREFIX, LEASE_MS,
)
from app.security.redis_quota import local_acl_redis_client

LIMIT_ENV = "METRIQEN_PROJECT_OUTPUT_RESERVATION_PER_MINUTE"
KEY_PREFIX = "metriqen:project-output:v1:"
WINDOW_SECONDS = 60
MIN_LIMIT = 256
MAX_LIMIT = 1000000
MAX_REQUEST_TOKENS = 2048

# Redis server TIME and all checks/spends are atomic across API replicas.
# One random 128-bit, live in-flight permit can spend at most once.
# No unverified upstream 'usage' is used; the full requested cap is spent.
# A denied request never increments/extends budget. Replay receipts survive
# release until the permit can no longer be live (lease 240 seconds).
RESERVE_SCRIPT = """
local now = redis.call('TIME')
local ms = tonumber(now[1])*1000 + math.floor(tonumber(now[2])/1000)
local expiry = redis.call('ZSCORE', KEYS[1], ARGV[1])
if expiry == false or tonumber(expiry) <= ms then
    return -1
end
if redis.call('EXISTS', KEYS[3]) == 1 then
    return -2
end
local spent = redis.call('GET', KEYS[2])
local current = 0
if spent ~= false then
    current = tonumber(spent)
    if current == nil or current < 0 then
        return redis.error_reply('corrupt output reservation counter')
    end
end
local amount = tonumber(ARGV[2])
local ceiling = tonumber(ARGV[3])
if amount == nil or amount < 1 or amount > 2048 or
   ceiling == nil or ceiling < 256 or ceiling > 1000000 then
    return redis.error_reply('invalid fixed budget configuration')
end
if current + amount > ceiling then
    return 0
end
local value = redis.call('INCRBY', KEYS[2], amount)
if current == 0 then
    redis.call('EXPIRE', KEYS[2], 60)
end
redis.call('SET', KEYS[3], '1', 'PX', tonumber(ARGV[4]) + 5000)
return 1
"""


def _is_hex(value: str, size: int) -> bool:
    return isinstance(value, str) and len(value) == size and all(
        ch in "0123456789abcdef" for ch in value
    )


class RedisProjectOutputReservations:
    def __init__(self, active: RedisProjectInflight, *, limit: int):
        if not isinstance(active, RedisProjectInflight):
            raise ValueError("output reservations require trusted active-inference Redis leases")
        if type(limit) is not int or not MIN_LIMIT <= limit <= MAX_LIMIT:
            raise ValueError("invalid output reservation per-minute budget")
        # Same trusted Redis/ACL context and HMAC identity as the active
        # lease. Never use another Redis server: verification and debit
        # MUST be one EVAL on a single trusted database.
        self._connection = active._connection
        self.limit = limit

    def reserve(self, scope: str, permit: str, requested_max_tokens: int) -> None:
        if (not _is_hex(scope, 64) or not _is_hex(permit, 32)
                or type(requested_max_tokens) is not int
                or not 1 <= requested_max_tokens <= MAX_REQUEST_TOKENS):
            raise HTTPException(422, "invalid requested output token cap")
        active_key = ACTIVE_KEY_PREFIX + scope
        budget_key = KEY_PREFIX + "budget:" + scope
        receipt_key = KEY_PREFIX + "receipt:" + scope + ":" + permit
        try:
            result = self._connection.eval(
                RESERVE_SCRIPT, 3, active_key, budget_key, receipt_key,
                permit, requested_max_tokens, self.limit, LEASE_MS,
            )
            if type(result) is not int or result not in (-2, -1, 0, 1):
                raise ValueError("unexpected Redis output reservation result")
        except Exception:
            raise HTTPException(503, "project output reservation unavailable") from None
        if result == 0:
            raise HTTPException(
                429, "project output reservation budget exceeded",
                headers={"Retry-After": str(WINDOW_SECONDS)},
            )
        if result in (-1, -2):
            raise HTTPException(409, "invalid or already reserved inference permit")
        # No refund (including upstream errors or client disconnect), by
        # design: actual token usage is NOT known to this control path.


def load_project_output_reservations(
    active: RedisProjectInflight | None,
    environ: Mapping[str, str] | None = None,
) -> RedisProjectOutputReservations | None:
    env = os.environ if environ is None else environ
    raw_limit = env.get(LIMIT_ENV)
    if raw_limit is None:
        return None
    if active is None:
        raise ValueError("project output reservations require active Redis project leases")
    if (not isinstance(raw_limit, str) or not raw_limit.isascii()
            or not raw_limit.isdecimal() or raw_limit.startswith("0")
            or len(raw_limit) > 7):
        raise ValueError("invalid per-project output reservation budget")
    try:
        limit = int(raw_limit)
    except ValueError:
        raise ValueError("invalid per-project output reservation budget") from None
    return RedisProjectOutputReservations(active, limit=limit)
