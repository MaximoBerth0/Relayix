from openai import AsyncOpenAI

from app.core.adapters.base import ProviderAdapter
from app.models.domain.chat import ChatRequest, ChatResponse
from app.models.domain.enums import ProviderEnum

# max_tokens is not required but is used to optimize costs
_DEFAULT_MAX_TOKENS = 4096

# translate OpenAI's finish_reason vocabulary into relayix's normalized set
_FINISH_REASON_MAP = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_use",
    "content_filter": "content_filter",
}


class OpenAIAdapter(ProviderAdapter):
    """talks to OpenAI's chat completions API and normalizes the result."""

    def __init__(self, api_key: str, timeout: float) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        messages = [
            {"role": m.role, "content": m.content}
            for m in request.messages
        ]

        response = await self._client.chat.completions.create(
            model=request.model,
            max_tokens=request.max_tokens or _DEFAULT_MAX_TOKENS,
            messages=messages,    # role system/assistant/user with content here
        )

        choice = response.choices[0]
        text = choice.message.content or ""

        return ChatResponse(
            provider=ProviderEnum.OPENAI,
            model=response.model,
            content=text,
            tokens_in=response.usage.prompt_tokens,
            tokens_out=response.usage.completion_tokens,
            finish_reason=_FINISH_REASON_MAP.get(choice.finish_reason, "stop"),
            request_id=response.id,
        )
