from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.core.accounting.pricing import PricingTable
from app.models.domain.chat import ChatResponse
from app.models.domain.usage_record import UsageRecord


class UsageRepository(Protocol):
    """Persistence port for usage records, implemented by the repositories layer."""

    async def save(self, record: UsageRecord) -> None:
        ...


class UsageRecorder:
    """Turns a completed request into a priced, validated usage record and stores it."""

    def __init__(self, pricing: PricingTable, repository: UsageRepository) -> None:
        self._pricing = pricing
        self._repository = repository

    async def record(self, api_key_id: UUID, response: ChatResponse) -> UsageRecord:
        """Price the response, build a UsageRecord and persist it."""
        cost = self._pricing.cost_for(
            provider=response.provider,
            model=response.model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )

        record = UsageRecord.create(
            api_key_id=api_key_id,
            provider=response.provider,
            model=response.model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost=cost,
            finish_reason=response.finish_reason,
            request_id=response.request_id,
        )

        await self._repository.save(record)
        return record
