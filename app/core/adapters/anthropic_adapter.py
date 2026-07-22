from anthropic import AnthropicError, AsyncAnthropic

from app.core.adapters.base import ProviderAdapter
from app.core.exceptions import UpstreamError
from app.models.domain.chat import ChatRequest, ChatResponse
from app.models.domain.enums import ProviderEnum

# Anthropic requires max_tokens
_DEFAULT_MAX_TOKENS = 4096

# translate Anthropic's stop_reason vocabulary into relayix's normalized set
_FINISH_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_use",
    "refusal": "content_filter",
}


class AnthropicAdapter(ProviderAdapter):
    """talks to Anthropic's messages API and normalizes the result."""

    def __init__(self, api_key: str, timeout: float) -> None:
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout)

    async def complete(self, request: ChatRequest) -> ChatResponse:
        system_prompt = "\n".join(
            m.content for m in request.messages if m.role == "system"
        )
        messages = [
            {"role": m.role, "content": m.content}
            for m in request.messages
            if m.role != "system"
        ]

        try:
            response = await self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens or _DEFAULT_MAX_TOKENS,
                system=system_prompt, # role system with content here
                messages=messages,    # role assistant/user with content here
            )
        except AnthropicError as exc:
            raise UpstreamError(f"anthropic request failed: {exc}") from exc

        # A 200 with a malformed/empty payload (missing content or usage) is still
        # an upstream failure 
        try:
            text = "".join(block.text for block in response.content if block.type == "text")

            return ChatResponse(
                provider=ProviderEnum.ANTHROPIC,
                model=response.model,
                content=text,
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                finish_reason=_FINISH_REASON_MAP.get(response.stop_reason, "stop"),
                request_id=response.id,
            )
        except (IndexError, AttributeError, TypeError) as exc:
            raise UpstreamError(f"anthropic returned a malformed response: {exc}") from exc
