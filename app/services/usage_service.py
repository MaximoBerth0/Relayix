from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.models.domain.usage_record import UsageRecord
from app.models.domain.usage_summary import UsageSummary


class UsageRepository(Protocol):
    """read port for usage data, implemented by the repositories layer."""

    async def summary_by_api_key(
        self,
        api_key_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> UsageSummary:
        ...

    async def list_by_api_key(
        self,
        api_key_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[UsageRecord]:
        ...


class UsageService:
    """application service for the usage read endpoints."""

    def __init__(self, repository: UsageRepository) -> None:
        self._repository = repository

    async def usage_summary(
        self,
        api_key_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> UsageSummary:
        """aggregate token and cost totals for one api key over a time window."""
        return await self._repository.summary_by_api_key(
            api_key_id,
            since=since,
            until=until,
        )

    async def list_usage_records(
        self,
        api_key_id: UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[UsageRecord]:
        """return the caller's usage records, most recent first."""
        return await self._repository.list_by_api_key(
            api_key_id,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
