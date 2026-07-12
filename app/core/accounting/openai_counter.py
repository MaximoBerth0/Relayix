import tiktoken

from app.core.accounting.token_counter import TokenCounter
from app.models.domain.chat import ChatRequest

_FALLBACK_ENCODING = "o200k_base"
_TOKENS_PER_MESSAGE = 3
_TOKENS_PER_REPLY = 3


class OpenAICounter(TokenCounter):
    """Counts prompt tokens for OpenAI models using tiktoken."""

    def count(self, request: ChatRequest) -> int:
        try:
            enc = tiktoken.encoding_for_model(request.model)
        except KeyError:
            enc = tiktoken.get_encoding(_FALLBACK_ENCODING)

        total = 0
        for message in request.messages:
            total += _TOKENS_PER_MESSAGE
            total += len(enc.encode(message.role))
            total += len(enc.encode(message.content))

        total += _TOKENS_PER_REPLY
        return total
