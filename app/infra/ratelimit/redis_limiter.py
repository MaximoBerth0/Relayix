"""Redis-backed token-bucket limiter, the source of truth across all workers"""

import time
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.exceptions import RateLimiterUnavailable, RateLimitExceeded

# KEYS[1] = bucket key
# ARGV[1] = capacity (max tokens)         ARGV[2] = refill rate (tokens/sec)
# ARGV[3] = now (unix seconds)            ARGV[4] = ttl seconds
# ARGV[5] = cost                          ARGV[6] = force (1 = charge without checking)
# returns {allowed (0|1), retry_after_seconds as string}
_TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local rate     = tonumber(ARGV[2])
local now      = tonumber(ARGV[3])
local ttl      = tonumber(ARGV[4])
local cost     = tonumber(ARGV[5])
local force    = tonumber(ARGV[6])

local state  = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts     = tonumber(state[2])

if tokens == nil then
  tokens = capacity
  ts = now
end

local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * rate)

local needed = math.min(cost, capacity)
local allowed = 0
local retry_after = 0
if force == 1 then
  tokens = math.min(capacity, tokens - cost)
  allowed = 1
elseif tokens >= needed then
  tokens = tokens - cost
  allowed = 1
else
  retry_after = (needed - tokens) / rate
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], ttl)

return {allowed, tostring(retry_after)}
"""


class RedisRateLimiter:
    """A `RateLimiter` whose buckets live in Redis, shared across every process.

    `namespace` keeps independent buckets for the same key apart (requests per
    minute under `ratelimit`, tokens per minute under `ratelimit:tpm`).
    """

    def __init__(self, client: Redis, namespace: str = "ratelimit") -> None:
        self._client = client
        self._namespace = namespace
        # register_script hashes the body once; calls use EVALSHA under the hood.
        self._script = client.register_script(_TOKEN_BUCKET_LUA)

    async def check(self, key: UUID, per_minute: int, cost: float = 1.0) -> None:
        allowed, retry_after_raw = await self._run(key, per_minute, cost, force=False)
        if not int(allowed):
            raise RateLimitExceeded(retry_after_s=float(retry_after_raw))

    async def charge(self, key: UUID, per_minute: int, cost: float) -> None:
        await self._run(key, per_minute, cost, force=True)

    async def _run(self, key: UUID, per_minute: int, cost: float, *, force: bool) -> list:
        capacity = float(per_minute)
        refill_rate = per_minute / 60.0
        now = time.time()
        # a bucket fully refills in 60s keep it around a bit longer, then let
        # idle keys expire so Redis doesn't accumulate dead buckets forever.
        ttl_s = 120

        try:
            return await self._script(
                keys=[f"{self._namespace}:{key}"],
                args=[capacity, refill_rate, now, ttl_s, cost, int(force)],
            )
        except (RedisError, OSError) as exc:
            # connection refused, timeout, Redis down, etc. -> let the resilient
            # wrapper fall back to the in-memory limiter.
            raise RateLimiterUnavailable() from exc
