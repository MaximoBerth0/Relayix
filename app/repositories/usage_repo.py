from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.usage_record import Usage_Record
from app.models.domain.usage_record import UsageRecord


class UsageRepo:
    """implementation of the UsageRepository port"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, record: UsageRecord) -> None:
        """Map the domain UsageRecord to its ORM row and persist it."""
        row = Usage_Record(
            api_key_id=record.api_key_id,
            provider=record.provider,
            model=record.model,
            token_in=record.tokens_in,
            token_out=record.tokens_out,
            finish_reason=record.finish_reason,
            cost=record.cost,
            request_id=record.request_id,
            created_at=record.created_at,
        )
        self._session.add(row)
        await self._session.flush()
