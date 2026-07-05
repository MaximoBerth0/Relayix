from dataclasses import dataclass
from app.models.domain.enums import ProviderEnum


@dataclass(frozen=True)
class Message:
    role: str          # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: list[Message]
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class ChatResponse:
    provider: ProviderEnum
    model: str
    content: str
    tokens_in: int
    tokens_out: int
    finish_reason: str
    request_id: str
