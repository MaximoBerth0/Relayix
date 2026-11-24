"""Token budget per api key: a tokens-per-minute bucket and a monthly quota.

Request-scoped: `admit` runs before the provider is called and remembers its
estimate, `settle` runs once the provider has answered and corrects both
limits with the real token count.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.core.accounting.registry import CounterRegistry
from app.core.exceptions import RateLimiterUnavailable, TokenQuotaExceeded
from app.core.ratelimit.base import RateLimiter
from app.core.routing.router import RoutingService
from app.models.domain.chat import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)


class TokenQuotaStore(Protocol):
    async def used(self, api_key_id: UUID, month: str) -> int | None: ...

    async def seed(self, api_key_id: UUID, month: str, tokens: int) -> None: ...

    async def add(self, api_key_id: UUID, month: str, tokens: int) -> None: ...


class UsageTotals(Protocol):
    async def tokens_since(self, api_key_id: UUID, since: datetime) -> int: ...


class TokenBudgetService:
    def __init__(
        self,
        api_key_id: UUID,
        tokens_per_minute: int,
        monthly_quota: int | None,
        limiter: RateLimiter,
        quota: TokenQuotaStore,
        usage: UsageTotals,
        router: RoutingService,
        counters: CounterRegistry,
    ) -> None:
        self._api_key_id = api_key_id
        self._tpm = tokens_per_minute
        self._monthly_quota = monthly_quota
        self._limiter = limiter
        self._quota = quota
        self._usage = usage
        self._router = router
        self._counters = counters
        self._estimate = 0

    async def admit(self, request: ChatRequest) -> None:
        """Reject the request before any provider is touched when the key's monthly
        quota or tokens-per-minute can't cover its estimated prompt.
        """
        estimate = self._estimate_prompt(request)
        if self._monthly_quota is not None:
            await self._check_monthly(estimate)
        await self._limiter.check(self._api_key_id, self._tpm, cost=estimate)
        self._estimate = estimate

    async def settle(self, response: ChatResponse) -> None:
        """Correct both limits with what the provider really counted. Never raises:
        the provider already answered, a budget hiccup must not fail the caller.
        """
        tokens = response.tokens_in + response.tokens_out
        try:
            await self._limiter.charge(self._api_key_id, self._tpm, tokens - self._estimate)
        except Exception:
            logger.exception("tokens-per-minute correction failed", extra={"api_key_id": str(self._api_key_id)})

        if self._monthly_quota is None:
            return
        try:
            await self._quota.add(self._api_key_id, _month(datetime.now(UTC)), tokens)
        except Exception:
            # the counter is reseeded from Postgres on its next miss
            logger.exception("monthly token counter update failed", extra={"api_key_id": str(self._api_key_id)})

    async def _check_monthly(self, estimate: int) -> None:
        now = datetime.now(UTC)
        month = _month(now)
        try:
            used = await self._quota.used(self._api_key_id, month)
            if used is None:
                used = await self._usage.tokens_since(self._api_key_id, _month_start(now))
                await self._quota.seed(self._api_key_id, month, used)
        except RateLimiterUnavailable:
            # a budget, not a security boundary: stay up and let the request through
            logger.warning("token quota store unavailable, skipping the monthly check", exc_info=True)
            return

        if used + estimate > self._monthly_quota:
            raise TokenQuotaExceeded(retry_after_s=(_next_month_start(now) - now).total_seconds())

    def _estimate_prompt(self, request: ChatRequest) -> int:
        """Prompt tokens as the tier's first candidate would count them. An unknown
        tier raises the same error the gateway would; a counter failure admits at 0.
        """
        first = self._router.candidates_for(request.model)[0]
        try:
            counter = self._counters.get(first.provider)
            return counter.count(replace(request, model=first.model))
        except Exception:
            logger.exception("prompt token estimate failed", extra={"tier": request.model})
            return 0


def _month(now: datetime) -> str:
    return f"{now:%Y-%m}"


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_start(now: datetime) -> datetime:
    start = _month_start(now)
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
