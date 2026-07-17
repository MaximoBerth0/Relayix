from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.core.adapters.base import ProviderAdapter
from app.core.exceptions import CircuitOpen, UpstreamError
from app.core.resilience.circuit_breaker import CircuitBreaker

if TYPE_CHECKING:
    from app.models.domain.chat import ChatRequest, ChatResponse
    from app.models.domain.enums import ProviderEnum


class ResilientAdapter(ProviderAdapter):
    """Wraps another adapter with a hard timeout and a circuit breaker, without
    changing the ProviderAdapter contract its callers rely on."""

    def __init__(
        self,
        inner: ProviderAdapter,
        provider: ProviderEnum,
        breaker: CircuitBreaker,
        timeout_s: float,
    ) -> None:
        self._inner = inner
        self._provider = provider
        self._breaker = breaker
        self._timeout_s = timeout_s

    async def complete(self, request: ChatRequest) -> ChatResponse:
        if not await self._breaker.allow():
            raise CircuitOpen(f"circuit open for provider {self._provider.value}")

        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._inner.complete(request)
        except TimeoutError as exc:
            await self._breaker.record_failure()
            raise UpstreamError(
                f"{self._provider.value} timed out after {self._timeout_s}s"
            ) from exc
        except UpstreamError:
            await self._breaker.record_failure()
            raise

        await self._breaker.record_success()
        return response
