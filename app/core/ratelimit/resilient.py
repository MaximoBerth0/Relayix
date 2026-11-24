"""the composer: Redis primary, in-memory fallback, guarded by a circuit breaker"""

import logging
from uuid import UUID

from app.core.exceptions import RateLimiterUnavailable, RateLimitExceeded
from app.core.ratelimit.base import RateLimiter
from app.core.resilience.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


class ResilientRateLimiter:
    """wraps a primary limiter (Redis) with a fallback limiter (in-memory)"""

    def __init__(
        self,
        primary: RateLimiter,
        fallback: RateLimiter,
        breaker: CircuitBreaker,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._breaker = breaker

    async def check(self, key: UUID, per_minute: int, cost: float = 1.0) -> None:
        allowed, generation = await self._breaker.allow()
        if not allowed:
            await self._fallback.check(key, per_minute, cost)
            return

        try:
            await self._primary.check(key, per_minute, cost)
        except RateLimitExceeded:
            await self._breaker.record_success(generation)
            raise
        except RateLimiterUnavailable as exc:
            await self._breaker.record_failure(generation)
            logger.warning("redis rate limiter unavailable, using in-memory fallback", exc_info=exc)
            await self._fallback.check(key, per_minute, cost)
            return

        # redis allowed the request
        await self._breaker.record_success(generation)

    async def charge(self, key: UUID, per_minute: int, cost: float) -> None:
        allowed, generation = await self._breaker.allow()
        if not allowed:
            await self._fallback.charge(key, per_minute, cost)
            return

        try:
            await self._primary.charge(key, per_minute, cost)
        except RateLimiterUnavailable as exc:
            await self._breaker.record_failure(generation)
            logger.warning("redis rate limiter unavailable, charging the in-memory fallback", exc_info=exc)
            await self._fallback.charge(key, per_minute, cost)
            return

        await self._breaker.record_success(generation)
