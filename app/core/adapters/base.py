from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from app.models.domain.chat import ChatRequest, ChatResponse, ChatStreamEvent


class ProviderAdapter(ABC):
    """Contract every provider (OpenAI, Anthropic) must implement"""

    @abstractmethod
    async def complete(self, request: ChatRequest) -> ChatResponse:
        """Send a normalized request to the provider and return a normalized response.

        Each concrete adapter is responsible for:
          1. translating `request` into the provider's own API format,
          2. calling the provider,
          3. translating the provider's raw response back into `ChatResponse`.
        """
        ...

    @abstractmethod
    def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        """Same contract as `complete`, but yields incremental `ChatStreamDelta`s
        as they arrive and finishes by yielding one `ChatResponse` with the full
        accumulated content and final usage.
        """
        ...
