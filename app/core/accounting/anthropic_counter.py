import math

from app.core.accounting.token_counter import TokenCounter
from app.models.domain.chat import ChatRequest

# Anthropic ships no local tokenizer 
_CHARS_PER_TOKEN = 3.5
_TOKENS_PER_MESSAGE = 3
_TOKENS_PER_REPLY = 3


class AnthropicCounter(TokenCounter):
    """Estimates prompt tokens for Anthropic models with a local heuristic"""

    def count(self, request: ChatRequest) -> int:
        total = 0
        for message in request.messages:
            total += _TOKENS_PER_MESSAGE
            total += math.ceil(len(message.content) / _CHARS_PER_TOKEN)
            total += math.ceil(len(message.role) / _CHARS_PER_TOKEN)

        total += _TOKENS_PER_REPLY
        return total
