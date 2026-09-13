"""Test doubles for the provider seam.

Everything on the request path is real except the provider's outbound HTTP
call. These adapters replace that single seam so a test can drive the whole
flow — or a specific failure mode — without touching OpenAI/Anthropic.
"""

from collections.abc import AsyncIterator

from app.core.adapters.base import ProviderAdapter
from app.models.domain.chat import (
    ChatRequest,
    ChatResponse,
    ChatStreamDelta,
    ChatStreamEvent,
)
from app.models.domain.enums import ProviderEnum


class StubAdapter(ProviderAdapter):
    """A ProviderAdapter that returns a canned response instead of calling out"""

    def __init__(self, provider: ProviderEnum, content: str = "stubbed reply") -> None:
        self._provider = provider
        self._content = content
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return self._response(request)

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        """Split into a few chunks that concatenate back to `_content` exactly,
        the same invariant a real adapter's accumulated deltas must satisfy.
        """
        self.calls.append(request)
        midpoint = max(1, len(self._content) // 2)
        for chunk in (self._content[:midpoint], self._content[midpoint:]):
            if chunk:
                yield ChatStreamDelta(content=chunk)
        yield self._response(request)

    def _response(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            provider=self._provider,
            model=request.model,
            content=self._content,
            tokens_in=12,
            tokens_out=8,
            finish_reason="stop",
            request_id=f"stub-{len(self.calls)}",
        )


class FailingAdapter(ProviderAdapter):
    """A ProviderAdapter that always raises, to drive failover and error paths.

    Records requests on `.calls` so a test can prove the provider was actually
    attempted before failing over to the next candidate.
    """

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        raise self._error

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        self.calls.append(request)
        raise self._error
        yield  # pragma: no cover - makes this an async generator function


class InterruptingAdapter(ProviderAdapter):
    """A ProviderAdapter that streams a few deltas, then breaks — for testing
    the case where content already reached the caller before failure, so the
    gateway can no longer fail over and must bill whatever partial usage it has.
    """

    def __init__(
        self,
        provider: ProviderEnum,
        error: Exception,
        deltas: list[str] | None = None,
    ) -> None:
        self._provider = provider
        self._error = error
        self._deltas = deltas if deltas is not None else ["par", "tial "]
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        raise self._error

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        self.calls.append(request)
        for piece in self._deltas:
            yield ChatStreamDelta(content=piece)
        raise self._error
