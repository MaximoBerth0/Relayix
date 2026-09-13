from dataclasses import dataclass

from app.models.domain.enums import FailoverPolicy, ProviderEnum


@dataclass(frozen=True)
class Message:
    role: str          # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: list[Message]
    max_tokens: int | None = None
    failover_policy: FailoverPolicy = FailoverPolicy.AT_MOST_ONCE


@dataclass(frozen=True)
class ChatResponse:
    provider: ProviderEnum
    model: str
    content: str
    tokens_in: int
    tokens_out: int
    finish_reason: str
    request_id: str


@dataclass(frozen=True)
class ChatStreamDelta:
    """One incremental piece of assistant text."""
    content: str


# An adapter's stream() yields zero or more deltas, then exactly one ChatResponse
# carrying the full accumulated content and final usage — the same terminal shape
# complete() returns, so callers that only care about the end result treat both
# code paths identically.
ChatStreamEvent = ChatStreamDelta | ChatResponse
