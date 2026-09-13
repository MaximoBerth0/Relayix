from collections.abc import AsyncIterator

from anthropic import (
    AnthropicError,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
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
        except APITimeoutError as exc:
            # the request was sent, we don't know if the model ran it.
            raise UpstreamAmbiguous(f"anthropic timed out: {exc}") from exc
        
        except APIConnectionError as exc:
            # never established a connection so the request never executed.
            raise UpstreamUnavailable(f"anthropic connection failed: {exc}") from exc
        
        except APIStatusError as exc:
            # a 5xx may have happened after the model ran. a 4xx is a pre-execution
            # rejection that never billed.
            if exc.status_code >= 500:
                raise UpstreamAmbiguous(
                    f"anthropic server error {exc.status_code}: {exc}"
                ) from exc
            raise UpstreamUnavailable(
                f"anthropic rejected request {exc.status_code}: {exc}"
            ) from exc
        
        except AnthropicError as exc:
            # unknown failure mode, be conservative and treat it as ambiguous.
            raise UpstreamAmbiguous(f"anthropic request failed: {exc}") from exc

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
            # a 200 we couldn't parse: the request DID execute and bill, so this
            # is ambiguous for failover, not a clean retry.
            raise UpstreamAmbiguous(f"anthropic returned a malformed response: {exc}") from exc

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        system_prompt = "\n".join(
            m.content for m in request.messages if m.role == "system"
        )
        messages = [
            {"role": m.role, "content": m.content}
            for m in request.messages
            if m.role != "system"
        ]

        content_parts: list[str] = []
        request_id: str | None = None
        model_name = request.model
        tokens_in = 0
        tokens_out = 0
        stop_reason: str | None = None

        try:
            async with self._client.messages.stream(
                model=request.model,
                max_tokens=request.max_tokens or _DEFAULT_MAX_TOKENS,
                system=system_prompt,
                messages=messages,
            ) as stream:
                async for event in stream:
                    if event.type == "message_start":
                        request_id = event.message.id
                        model_name = event.message.model
                        tokens_in = event.message.usage.input_tokens
                    elif (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                        and event.delta.text
                    ):
                        content_parts.append(event.delta.text)
                        yield ChatStreamDelta(content=event.delta.text)
                    elif event.type == "message_delta":
                        tokens_out = event.usage.output_tokens
                        if event.delta.stop_reason:
                            stop_reason = event.delta.stop_reason
        except APITimeoutError as exc:
            self._raise_stream_failure(
                exc, UpstreamAmbiguous(f"anthropic timed out: {exc}"),
                content_parts, tokens_in, tokens_out, request_id, model_name,
            )
        except APIConnectionError as exc:
            self._raise_stream_failure(
                exc, UpstreamUnavailable(f"anthropic connection failed: {exc}"),
                content_parts, tokens_in, tokens_out, request_id, model_name,
            )
        except APIStatusError as exc:
            clean_error = (
                UpstreamAmbiguous(f"anthropic server error {exc.status_code}: {exc}")
                if exc.status_code >= 500
                else UpstreamUnavailable(f"anthropic rejected request {exc.status_code}: {exc}")
            )
            self._raise_stream_failure(
                exc, clean_error, content_parts, tokens_in, tokens_out, request_id, model_name,
            )
        except AnthropicError as exc:
            self._raise_stream_failure(
                exc, UpstreamAmbiguous(f"anthropic request failed: {exc}"),
                content_parts, tokens_in, tokens_out, request_id, model_name,
            )

        yield ChatResponse(
            provider=ProviderEnum.ANTHROPIC,
            model=model_name,
            content="".join(content_parts),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            finish_reason=_FINISH_REASON_MAP.get(stop_reason, "stop"),
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
                f"anthropic stream broke after partial output: {exc}",
                partial=ChatResponse(
                    provider=ProviderEnum.ANTHROPIC,
                    model=model_name,
                    content="".join(content_parts),
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    finish_reason="error",
                    request_id=request_id or "",
                ),
            ) from exc
        raise clean_error from exc
