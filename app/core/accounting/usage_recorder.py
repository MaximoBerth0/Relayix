from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from app.core.accounting.pricing import PricingTable
from app.models.domain.chat import ChatResponse
from app.models.domain.usage_record import UsageRecord

logger = logging.getLogger(__name__)


class UsageRepository(Protocol):
    """Persistence port for usage records, implemented by the repositories layer."""

    async def save(self, record: UsageRecord) -> None:
        ...


class PendingUsageQueue(Protocol):
    """Where a record goes when its insert fails, for the retry worker to persist."""

    async def push(self, record: UsageRecord) -> None:
        ...


class UsageRecorder:
    """Turns a completed request into a priced, validated usage record and stores it."""

    def __init__(
        self,
        pricing: PricingTable,
        repository: UsageRepository,
        pending: PendingUsageQueue,
    ) -> None:
        self._pricing = pricing
        self._repository = repository
        self._pending = pending

    async def record(self, api_key_id: UUID, response: ChatResponse, model: str) -> UsageRecord:
        """Price the response by `model`, the catalog name the gateway asked for.

        The provider may report a different name (OpenAI answers with dated
        snapshots), so that one is only kept as `upstream_model`.
        """
        cost = self._pricing.cost_for(
            provider=response.provider,
            model=model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )

        record = UsageRecord.create(
            api_key_id=api_key_id,
            provider=response.provider,
            model=model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost=cost,
            finish_reason=response.finish_reason,
            request_id=response.request_id,
            upstream_model=response.model,
        )

        try:
            await self._repository.save(record)
        except Exception:
            # pricing and validation are deterministic, only the insert is worth retrying
            logger.warning(
                "usage insert failed, queued for retry",
                extra={"usage_id": str(record.id), "upstream_request_id": record.request_id},
                exc_info=True,
            )
            await self._pending.push(record)
        return record
