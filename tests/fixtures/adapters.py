"""Test doubles for the provider seam.

Everything on the request path is real except the provider's outbound HTTP
call. These adapters replace that single seam so a test can drive the whole
flow — or a specific failure mode — without touching OpenAI/Anthropic.
"""

from app.core.adapters.base import ProviderAdapter
from app.models.domain.chat import ChatRequest, ChatResponse
from app.models.domain.enums import ProviderEnum


class StubAdapter(ProviderAdapter):
    """A ProviderAdapter that returns a canned response instead of calling out"""

    def __init__(self, provider: ProviderEnum, content: str = "stubbed reply") -> None:
        self._provider = provider
        self._content = content
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
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
