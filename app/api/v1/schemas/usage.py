"""transport schemas for the usage read endpoints"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.models.domain.enums import ProviderEnum
from app.models.domain.usage_record import UsageRecord

if TYPE_CHECKING:
    from app.models.domain.usage_summary import UsageSummary


class UsageRecordSchema(BaseModel):
    provider: ProviderEnum
    model: str
    tokens_in: int
    tokens_out: int
    cost: Decimal
    finish_reason: str
    request_id: str
    created_at: datetime

    @classmethod
    def from_domain(cls, record: UsageRecord) -> "UsageRecordSchema":
        return cls(
            provider=record.provider,
            model=record.model,
            tokens_in=record.tokens_in,
            tokens_out=record.tokens_out,
            cost=record.cost,
            finish_reason=record.finish_reason,
            request_id=record.request_id,
            created_at=record.created_at,
        )


class UsageSummarySchema(BaseModel):
    total_requests: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost: Decimal
    since: datetime | None = None
    until: datetime | None = None

    @classmethod
    def from_domain(cls, summary: "UsageSummary") -> "UsageSummarySchema":
        return cls(
            total_requests=summary.total_requests,
            total_tokens_in=summary.total_tokens_in,
            total_tokens_out=summary.total_tokens_out,
            total_cost=summary.total_cost,
            since=summary.since,
            until=summary.until,
        )
