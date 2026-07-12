import tiktoken

from app.core.accounting.token_counter import TokenCounter
from app.models.domain.chat import ChatRequest

# fallback encoding when a model name isn't known to tiktoken yet (e.g. a
# freshly released model). o200k_base backs the current GPT-4o family.
_FALLBACK_ENCODING = "o200k_base"

# per the OpenAI chat-format overhead: every message is wrapped with a few
# structural tokens, and every response is primed with the assistant role.
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
