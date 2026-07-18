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

    async def check(self, key: UUID, rpm: int) -> None:
        if not await self._breaker.allow():
            await self._fallback.check(key, rpm)
            return

        try:
            await self._primary.check(key, rpm)
        except RateLimitExceeded:
            await self._breaker.record_success()
            raise
        except RateLimiterUnavailable as exc:
            await self._breaker.record_failure()
            logger.warning("redis rate limiter unavailable, using in-memory fallback", exc_info=exc)
            await self._fallback.check(key, rpm)
            return

        # redis allowed the request
        await self._breaker.record_success()
