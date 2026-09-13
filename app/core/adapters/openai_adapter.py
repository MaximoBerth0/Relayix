from collections.abc import AsyncIterator

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
)

from app.core.adapters.base import ProviderAdapter
from app.core.exceptions import (
    UpstreamAmbiguous,
    UpstreamStreamInterrupted,
    UpstreamUnavailable,
)
from app.models.domain.chat import (
    ChatRequest,
    ChatResponse,
    ChatStreamDelta,
    ChatStreamEvent,
)
from app.models.domain.enums import ProviderEnum

# not required by the API but sent to optimize costs. 
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
                max_completion_tokens=request.max_tokens or _DEFAULT_MAX_TOKENS,
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

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        messages = [
            {"role": m.role, "content": m.content}
            for m in request.messages
        ]

        content_parts: list[str] = []
        request_id: str | None = None
        model_name = request.model
        tokens_in = 0
        tokens_out = 0
        finish_reason_raw: str | None = None

        try:
            response_stream = await self._client.chat.completions.create(
                model=request.model,
                max_completion_tokens=request.max_tokens or _DEFAULT_MAX_TOKENS,
                messages=messages,
                stream=True,
                # without this, usage is never sent in any chunk.
                stream_options={"include_usage": True},
            )
            async for chunk in response_stream:
                request_id = chunk.id
                model_name = chunk.model
                if chunk.usage is not None:
                    tokens_in = chunk.usage.prompt_tokens
                    tokens_out = chunk.usage.completion_tokens
                if not chunk.choices:
                    # the trailing usage-only chunk has no choices.
                    continue
                choice = chunk.choices[0]
                if choice.delta.content:
                    content_parts.append(choice.delta.content)
                    yield ChatStreamDelta(content=choice.delta.content)
                if choice.finish_reason:
                    finish_reason_raw = choice.finish_reason
        except APITimeoutError as exc:
            self._raise_stream_failure(
                exc, UpstreamAmbiguous(f"openai timed out: {exc}"),
                content_parts, tokens_in, tokens_out, request_id, model_name,
            )
        except APIConnectionError as exc:
            self._raise_stream_failure(
                exc, UpstreamUnavailable(f"openai connection failed: {exc}"),
                content_parts, tokens_in, tokens_out, request_id, model_name,
            )
        except APIStatusError as exc:
            clean_error = (
                UpstreamAmbiguous(f"openai server error {exc.status_code}: {exc}")
                if exc.status_code >= 500
                else UpstreamUnavailable(f"openai rejected request {exc.status_code}: {exc}")
            )
            self._raise_stream_failure(
                exc, clean_error, content_parts, tokens_in, tokens_out, request_id, model_name,
            )
        except OpenAIError as exc:
            self._raise_stream_failure(
                exc, UpstreamAmbiguous(f"openai request failed: {exc}"),
                content_parts, tokens_in, tokens_out, request_id, model_name,
            )

        yield ChatResponse(
            provider=ProviderEnum.OPENAI,
            model=model_name,
            content="".join(content_parts),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            finish_reason=_FINISH_REASON_MAP.get(finish_reason_raw, "stop"),
            request_id=request_id or "",
        )

    @staticmethod
    def _raise_stream_failure(
        exc: Exception,
        clean_error: Exception,
        content_parts: list[str],
        tokens_in: int,
        tokens_out: int,
        request_id: str | None,
        model_name: str,
    ) -> None:
        """Content already sent to the caller can't be retried elsewhere: once
        `content_parts` is non-empty, always surface `UpstreamStreamInterrupted`
        with the partial usage attached, regardless of what the clean (pre-first-
        byte) classification would have been. 
        """
        if content_parts:
            raise UpstreamStreamInterrupted(
                f"openai stream broke after partial output: {exc}",
                partial=ChatResponse(
                    provider=ProviderEnum.OPENAI,
                    model=model_name,
                    content="".join(content_parts),
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    finish_reason="error",
                    request_id=request_id or "",
                ),
            ) from exc
        raise clean_error from exc
