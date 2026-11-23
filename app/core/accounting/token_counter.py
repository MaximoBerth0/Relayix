from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.domain.chat import ChatRequest


class TokenCounter(ABC):
    """contract every provider's tokenizer (OpenAI, Anthropic) must implement.
    """

    @abstractmethod
    def count(self, request: ChatRequest) -> int:
        """Return the number of prompt tokens `request` will consume.
        """
        ...

    @abstractmethod
    def count_completion(self, model: str, content: str) -> int:
        """Return the number of output tokens `content` cost on `model`."""
        ...
