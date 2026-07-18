"""Redis-backed token-bucket limiter, the source of truth across all workers"""

import time
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.exceptions import RateLimiterUnavailable, RateLimitExceeded

# KEYS[1] = bucket key
# ARGV[1] = capacity (max tokens = rpm)   ARGV[2] = refill rate (tokens/sec)
# ARGV[3] = now (unix seconds)            ARGV[4] = ttl seconds
# returns {allowed (0|1), retry_after_seconds as string}
_TOKEN_BUCKET_LUA = """
local capacity = tonumber(ARGV[1])
local rate     = tonumber(ARGV[2])
local now      = tonumber(ARGV[3])
local ttl      = tonumber(ARGV[4])

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

local allowed = 0
local retry_after = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_after = (1 - tokens) / rate
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], ttl)

return {allowed, tostring(retry_after)}
"""


class RedisRateLimiter:
    """A `RateLimiter` whose buckets live in Redis, shared across every process."""

    def __init__(self, client: Redis) -> None:
        self._client = client
        # register_script hashes the body once; calls use EVALSHA under the hood.
        self._script = client.register_script(_TOKEN_BUCKET_LUA)

    async def check(self, key: UUID, rpm: int) -> None:
        capacity = float(rpm)
        refill_rate = rpm / 60.0
        now = time.time()
        # a bucket fully refills in 60s keep it around a bit longer, then let
        # idle keys expire so Redis doesn't accumulate dead buckets forever.
        ttl_s = 120

        try:
            allowed, retry_after_raw = await self._script(
                keys=[f"ratelimit:{key}"],
                args=[capacity, refill_rate, now, ttl_s],
            )
        except (RedisError, OSError) as exc:
            # connection refused, timeout, Redis down, etc. -> let the resilient
            # wrapper fall back to the in-memory limiter.
            raise RateLimiterUnavailable() from exc

        if not int(allowed):
            retry_after_s = float(retry_after_raw)
            raise RateLimitExceeded(retry_after_s=retry_after_s)
