"""Monthly token counter per api key, in Redis.

One key per api key and month (`quota:{api_key_id}:{YYYY-MM}`), so a new month
starts from zero without any reset job. Postgres stays the source of truth: a
missing counter is seeded from `usage_record`, never assumed to be zero.
"""

from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.exceptions import RateLimiterUnavailable

# longer than any month, the counter only has to outlive its own month
_TTL_S = 35 * 86_400

# INCRBY only an existing counter: creating it here would start it from 0 and
# hide the month's earlier usage from the seed
_ADD_IF_EXISTS_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return redis.call('INCRBY', KEYS[1], ARGV[1])
end
return nil
"""


class RedisTokenQuota:
    def __init__(self, client: Redis) -> None:
        self._client = client
        self._add_if_exists = client.register_script(_ADD_IF_EXISTS_LUA)

    async def used(self, api_key_id: UUID, month: str) -> int | None:
        """Tokens counted this month, or None when the counter doesn't exist yet."""
        try:
            raw = await self._client.get(_key(api_key_id, month))
        except (RedisError, OSError) as exc:
            raise RateLimiterUnavailable() from exc
        return None if raw is None else int(raw)

    async def seed(self, api_key_id: UUID, month: str, tokens: int) -> None:
        """Create the counter unless a concurrent request already did."""
        try:
            await self._client.set(_key(api_key_id, month), tokens, nx=True, ex=_TTL_S)
        except (RedisError, OSError) as exc:
            raise RateLimiterUnavailable() from exc

    async def add(self, api_key_id: UUID, month: str, tokens: int) -> None:
        try:
            await self._add_if_exists(keys=[_key(api_key_id, month)], args=[tokens])
        except (RedisError, OSError) as exc:
            raise RateLimiterUnavailable() from exc


def _key(api_key_id: UUID, month: str) -> str:
    return f"quota:{api_key_id}:{month}"
