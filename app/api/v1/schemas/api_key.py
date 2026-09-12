"""transport schemas for the api-key management endpoints"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.models.domain.api_key import ApiKey


class ApiKeyCreateRequestSchema(BaseModel):
    name: str = Field(min_length=1)
    rate_limit_rpm: int | None = Field(default=None, gt=0)
    monthly_token_quota: int | None = Field(default=None, gt=0)


class ApiKeySchema(BaseModel):
    id: UUID
    name: str
    rate_limit_rpm: int | None
    monthly_token_quota: int | None
    is_active: bool
    created_at: datetime

    @classmethod
    def from_domain(cls, api_key: "ApiKey") -> "ApiKeySchema":
        return cls(
            id=api_key.id,
            name=api_key.name,
            rate_limit_rpm=api_key.rate_limit_rpm,
            monthly_token_quota=api_key.monthly_token_quota,
            is_active=api_key.is_active,
            created_at=api_key.created_at,
        )


class ApiKeyCreateResponseSchema(ApiKeySchema):
    api_key: str

    @classmethod
    def from_domain(cls, api_key: "ApiKey", token: str) -> "ApiKeyCreateResponseSchema":
        return cls(
            id=api_key.id,
            name=api_key.name,
            rate_limit_rpm=api_key.rate_limit_rpm,
            monthly_token_quota=api_key.monthly_token_quota,
            is_active=api_key.is_active,
            created_at=api_key.created_at,
            api_key=token,
        )
