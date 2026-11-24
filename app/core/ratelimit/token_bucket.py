"""In-memory token-bucket limiter"""

import asyncio
import time
from dataclasses import dataclass
from uuid import UUID

from app.core.exceptions import RateLimitExceeded


@dataclass
class _Bucket:
    tokens: float
    last_refill: float  # monotonic timestamp of the last refill


class InMemoryRateLimiter:
    """A `RateLimiter` backed by a dict of buckets guarded by a single lock."""

    def __init__(self) -> None:
        self._buckets: dict[UUID, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: UUID, per_minute: int, cost: float = 1.0) -> None:
        capacity = float(per_minute)
        refill_rate = per_minute / 60.0  # tokens per second
        # a cost bigger than the bucket only needs a full one, or it never passes
        needed = min(cost, capacity)

        async with self._lock:
            bucket = self._refilled(key, capacity, refill_rate)

            if bucket.tokens >= needed:
                bucket.tokens -= cost
                return

            # not enough yet: tell the caller how long until there is.
            retry_after_s = (needed - bucket.tokens) / refill_rate
            raise RateLimitExceeded(retry_after_s=retry_after_s)

    async def charge(self, key: UUID, per_minute: int, cost: float) -> None:
        capacity = float(per_minute)
        async with self._lock:
            bucket = self._refilled(key, capacity, per_minute / 60.0)
            bucket.tokens = min(capacity, bucket.tokens - cost)

    def _refilled(self, key: UUID, capacity: float, refill_rate: float) -> _Bucket:
        """The key's bucket, refilled for the time elapsed since it was last touched,
        capped at capacity so idle time can't bank unlimited burst. Caller holds the lock.
        """
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            # a brand-new caller starts with a full bucket
            bucket = _Bucket(tokens=capacity, last_refill=now)
            self._buckets[key] = bucket

        elapsed = now - bucket.last_refill
        bucket.tokens = min(capacity, bucket.tokens + elapsed * refill_rate)
        bucket.last_refill = now
        return bucket
