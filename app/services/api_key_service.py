from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.infra.security.crypto import generate_api_key, hash_api_key
from app.models.domain.api_key import ApiKey
from app.models.domain.exceptions import ApiKeyNotFound


class ApiKeyRepository(Protocol):
    """persistence port for api-key management, implemented by the repositories layer."""

    async def create(self, api_key: ApiKey) -> None:
        ...

    async def list_all(self) -> list[ApiKey]:
        ...

    async def revoke(self, api_key_id: UUID) -> ApiKey | None:
        ...


class ApiKeyService:
    """application service for the api-key management endpoints."""

    def __init__(self, repository: ApiKeyRepository) -> None:
        self._repository = repository

    async def create_api_key(
        self,
        name: str,
        *,
        rate_limit_rpm: int | None = None,
        monthly_token_quota: int | None = None,
    ) -> tuple[ApiKey, str]:
        """create a new api key. The raw token is returned once, alongside the
        record, and is never persisted or retrievable again."""
        token = generate_api_key()
        api_key = ApiKey.create(
            key_hash=hash_api_key(token),
            name=name,
            rate_limit_rpm=rate_limit_rpm,
            monthly_token_quota=monthly_token_quota,
        )
        await self._repository.create(api_key)
        return api_key, token

    async def list_api_keys(self) -> list[ApiKey]:
        """return every api key, most recently created first."""
        return await self._repository.list_all()

    async def revoke_api_key(self, api_key_id: UUID) -> ApiKey:
        """deactivate an api key by id."""
        api_key = await self._repository.revoke(api_key_id)
        if api_key is None:
            raise ApiKeyNotFound()
        return api_key
