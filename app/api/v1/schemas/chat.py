"""
"app.models.domain.chat" happens through `to_domain()` / `from_domain()` so
the transport layer never leaks into the service layer.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.models.domain.chat import ChatRequest, ChatResponse, Message
from app.models.domain.enums import FailoverPolicy, ProviderEnum


class MessageSchema(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)

    def to_domain(self) -> Message:
        return Message(role=self.role, content=self.content)


class ChatRequestSchema(BaseModel):
    model: str = Field(min_length=1)
    messages: list[MessageSchema] = Field(min_length=1)
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    failover_policy: FailoverPolicy = Field(default=FailoverPolicy.AT_MOST_ONCE)

    def to_domain(self) -> ChatRequest:
        return ChatRequest(
            model=self.model,
            messages=[message.to_domain() for message in self.messages],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            failover_policy=self.failover_policy,
        )


class ChatResponseSchema(BaseModel):
    provider: ProviderEnum
    model: str
    content: str
    tokens_in: int
    tokens_out: int
    finish_reason: str
    request_id: str

    @classmethod
    def from_domain(cls, response: ChatResponse) -> "ChatResponseSchema":
        return cls(
            provider=response.provider,
            model=response.model,
            content=response.content,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            finish_reason=response.finish_reason,
            request_id=response.request_id,
        )
