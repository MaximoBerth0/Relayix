from dataclasses import dataclass
from datetime import datetime, timezone
from app.models.domain.exceptions import (
    InvalidToken,
    InvalidApiKeyName,
    InvalidRateLimit,
    InvalidTokenQuota,
)

@dataclass(frozen=True)
class ApiKey:
    key_hash: str
    name: str
    rate_limit_rpm: int | None
    monthly_token_quota: int | None
    created_at: datetime

    @classmethod
    def create(
        cls,
        key_hash: str,
        name: str,
        rate_limit_rpm: int | None = None,
        monthly_token_quota: int | None = None,
    ) -> "ApiKey":

        if not key_hash.strip():
            raise InvalidToken()

        if not name.strip():
            raise InvalidApiKeyName()

        if rate_limit_rpm is not None and rate_limit_rpm <= 0:
            raise InvalidRateLimit()

        if monthly_token_quota is not None and monthly_token_quota <= 0:
            raise InvalidTokenQuota()

        return cls(
            key_hash=key_hash,
            name=name,
            rate_limit_rpm=rate_limit_rpm,
            monthly_token_quota=monthly_token_quota,
            created_at=datetime.now(timezone.utc),
        )
