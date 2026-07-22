from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
)

from app.core.adapters.base import ProviderAdapter
from app.core.exceptions import UpstreamAmbiguous, UpstreamUnavailable
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

        try:
            response = await self._client.chat.completions.create(
                model=request.model,
                max_tokens=request.max_tokens or _DEFAULT_MAX_TOKENS,
                messages=messages,    # role system/assistant/user with content here
            )

        except APITimeoutError as exc:
            # the request was sent, we don't know if the model ran it.
            raise UpstreamAmbiguous(f"openai timed out: {exc}") from exc
        
        except APIConnectionError as exc:
            # never established a connection so the request never executed.
            raise UpstreamUnavailable(f"openai connection failed: {exc}") from exc
        
        except APIStatusError as exc:
            # a 5xx may have happened after the model ran. a 4xx is a pre-execution
            # rejection that never billed.
            if exc.status_code >= 500:
                raise UpstreamAmbiguous(
                    f"openai server error {exc.status_code}: {exc}"
                ) from exc
            raise UpstreamUnavailable(
                f"openai rejected request {exc.status_code}: {exc}"
            ) from exc
        
        except OpenAIError as exc:
            # unknown failure mode: be conservative and treat it as ambiguous.
            raise UpstreamAmbiguous(f"openai request failed: {exc}") from exc

        try:
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
        except (IndexError, AttributeError, TypeError) as exc:
            # a 200 we couldn't parse, the request DID execute and bill, so this
            # is ambiguous for failover, not a clean retry.
            raise UpstreamAmbiguous(f"openai returned a malformed response: {exc}") from exc
