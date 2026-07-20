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

    async def check(self, key: UUID, rpm: int) -> None:
        capacity = float(rpm)
        refill_rate = rpm / 60.0  # tokens per second

        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(key)
            if bucket is None:
                # a brand-new caller starts with a full bucket
                bucket = _Bucket(tokens=capacity, last_refill=now)
                self._buckets[key] = bucket

            # refill for the time elapsed since we last touched this bucket,
            # capping at capacity so idle time can't bank unlimited burst.
            elapsed = now - bucket.last_refill
            bucket.tokens = min(capacity, bucket.tokens + elapsed * refill_rate)
            bucket.last_refill = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return

            # not enough for one token: tell the caller how long until there is.
            retry_after_s = (1.0 - bucket.tokens) / refill_rate
            raise RateLimitExceeded(retry_after_s=retry_after_s)
