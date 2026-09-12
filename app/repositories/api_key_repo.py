from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.api_key import Api_Key
from app.models.domain.api_key import ApiKey


class ApiKeyRepo:
    """implementation of the api-key management persistence port"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, api_key: ApiKey) -> None:
        """Map the domain ApiKey to its ORM row and persist it."""
        row = Api_Key(
            id=api_key.id,
            key_hash=api_key.key_hash,
            name=api_key.name,
            rate_limit_rpm=api_key.rate_limit_rpm,
            monthly_token_quota=api_key.monthly_token_quota,
            is_active=api_key.is_active,
            created_at=api_key.created_at,
        )
        self._session.add(row)
        await self._session.flush()

    async def list_all(self) -> list[ApiKey]:
        """Return every api key, most recently created first."""
        stmt = select(Api_Key).order_by(Api_Key.created_at.desc())
        rows = (await self._session.scalars(stmt)).all()
        return [self._to_domain(row) for row in rows]

    async def revoke(self, api_key_id: UUID) -> ApiKey | None:
        """Deactivate an api key by id. Returns None if it doesn't exist."""
        row = await self._session.get(Api_Key, api_key_id)
        if row is None:
            return None

        row.is_active = False
        await self._session.flush()
        return self._to_domain(row)

    @staticmethod
    def _to_domain(row: Api_Key) -> ApiKey:
        """Map an ORM row back to its domain ApiKey."""
        return ApiKey(
            id=row.id,
            key_hash=row.key_hash,
            name=row.name,
            rate_limit_rpm=row.rate_limit_rpm,
            monthly_token_quota=row.monthly_token_quota,
            is_active=row.is_active,
            created_at=row.created_at,
        )
