from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.domain.chat import ChatRequest


class TokenCounter(ABC):
    """Contract every provider's tokenizer (OpenAI, Anthropic) must implement.

    Used to estimate the token count of a request *before* it is dispatched,
    so cost accounting and rate limiting can act on it up front. The rest of
    the system only ever talks to this interface, never to a concrete
    tokenizer
    """

    @abstractmethod
    def count(self, request: ChatRequest) -> int:
        """Return the number of prompt tokens `request` will consume.
        """
        ...
