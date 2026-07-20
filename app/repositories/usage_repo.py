from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.usage_record import Usage_Record
from app.models.domain.enums import ProviderEnum
from app.models.domain.usage_record import UsageRecord
from app.models.domain.usage_summary import UsageSummary


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

    async def summary_by_api_key(
        self,
        api_key_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> UsageSummary:
        """aggregate token and cost totals for one api key over a time window."""
        stmt = select(
            func.count(Usage_Record.id),
            func.coalesce(func.sum(Usage_Record.token_in), 0),
            func.coalesce(func.sum(Usage_Record.token_out), 0),
            func.coalesce(func.sum(Usage_Record.cost), 0),
        ).where(Usage_Record.api_key_id == api_key_id)
        stmt = self._apply_window(stmt, since=since, until=until)

        row = (await self._session.execute(stmt)).one()
        total_requests, total_tokens_in, total_tokens_out, total_cost = row

        return UsageSummary(
            total_requests=int(total_requests),
            total_tokens_in=int(total_tokens_in),
            total_tokens_out=int(total_tokens_out),
            total_cost=Decimal(total_cost),
            since=since,
            until=until,
        )

    async def list_by_api_key(
        self,
        api_key_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[UsageRecord]:
        """Return the caller's usage records, most recent first."""
        stmt = select(Usage_Record).where(Usage_Record.api_key_id == api_key_id)
        stmt = self._apply_window(stmt, since=since, until=until)
        stmt = stmt.order_by(Usage_Record.created_at.desc()).limit(limit).offset(offset)

        rows = (await self._session.scalars(stmt)).all()
        return [self._to_domain(row) for row in rows]

    @staticmethod
    def _apply_window(stmt, *, since: datetime | None, until: datetime | None):
        """Constrain a statement to the [since, until] created_at window."""
        if since is not None:
            stmt = stmt.where(Usage_Record.created_at >= since)
        if until is not None:
            stmt = stmt.where(Usage_Record.created_at <= until)
        return stmt

    @staticmethod
    def _to_domain(row: Usage_Record) -> UsageRecord:
        """Map an ORM row back to its domain UsageRecord."""
        return UsageRecord(
            api_key_id=row.api_key_id,
            provider=ProviderEnum(row.provider),
            model=row.model,
            tokens_in=row.token_in,
            tokens_out=row.token_out,
            cost=row.cost,
            finish_reason=row.finish_reason,
            request_id=row.request_id,
            created_at=row.created_at,
        )
