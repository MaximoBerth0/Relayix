from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from app.core.adapters.base import ProviderAdapter
from app.core.exceptions import (
    CircuitOpen,
    UpstreamAmbiguous,
    UpstreamStreamInterrupted,
)
from app.core.resilience.circuit_breaker import CircuitBreaker
from app.models.domain.chat import ChatResponse

if TYPE_CHECKING:
    from app.models.domain.chat import ChatRequest, ChatStreamEvent
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
        allowed, generation = await self._breaker.allow()
        if not allowed:
            raise CircuitOpen(f"circuit open for provider {self._provider.value}")

        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._inner.complete(request)

        except TimeoutError as exc:
            # Ambiguous, not safe to retry
            # blindly on another provider.
            await self._breaker.record_failure(generation)
            raise UpstreamAmbiguous(
                f"{self._provider.value} timed out after {self._timeout_s}s"
            ) from exc

        except Exception:
            await self._breaker.record_failure(generation)
            raise

        except BaseException:
            await self._breaker.record_abort(generation)
            raise

        await self._breaker.record_success(generation)
        return response

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        allowed, generation = await self._breaker.allow()
        if not allowed:
            raise CircuitOpen(f"circuit open for provider {self._provider.value}")

        inner_iter = self._inner.stream(request)
        emitted_any = False
        try:
            while True:
                try:
                    async with asyncio.timeout(self._timeout_s):
                        event = await inner_iter.__anext__()
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    # A stall: no event (first token or otherwise) within the
                    # window. Ambiguous if nothing reached the caller yet;
                    # otherwise the caller already has partial content and
                    # can't be failed over. Recorded once below, by the outer
                    # `except Exception` this re-raise falls into.
                    if emitted_any:
                        raise UpstreamStreamInterrupted(
                            f"{self._provider.value} stalled mid-stream after "
                            f"{self._timeout_s}s"
                        ) from exc
                    raise UpstreamAmbiguous(
                        f"{self._provider.value} timed out after {self._timeout_s}s"
                    ) from exc

                if isinstance(event, ChatResponse):
                    yield event
                    await self._breaker.record_success(generation)
                    return
                emitted_any = True
                yield event
        except Exception:
            await self._breaker.record_failure(generation)
            raise
        except BaseException:
            await self._breaker.record_abort(generation)
            raise
        finally:
            await inner_iter.aclose()
