import tiktoken

from app.core.accounting.token_counter import TokenCounter
from app.models.domain.chat import ChatRequest

_FALLBACK_ENCODING = "o200k_base"
_TOKENS_PER_MESSAGE = 3
_TOKENS_PER_REPLY = 3


class OpenAICounter(TokenCounter):
    """Counts prompt tokens for OpenAI models using tiktoken."""

    def count(self, request: ChatRequest) -> int:
        enc = _encoding_for(request.model)
        total = 0
        for message in request.messages:
            total += _TOKENS_PER_MESSAGE
            total += len(enc.encode(message.role))
            total += len(enc.encode(message.content))

        total += _TOKENS_PER_REPLY
        return total

    def count_completion(self, model: str, content: str) -> int:
        return len(_encoding_for(model).encode(content))


def _encoding_for(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding(_FALLBACK_ENCODING)
