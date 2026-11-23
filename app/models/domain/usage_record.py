from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, timezone
from uuid import UUID, uuid7
from app.models.domain.enums import ProviderEnum, VALID_FINISH_REASONS
from app.models.domain.exceptions import InvalidTokenQuantity, InvalidFinishReason, InvalidUsageCost


@dataclass(frozen=True)
class UsageRecord:
    api_key_id: UUID
    provider: ProviderEnum
    model: str
    tokens_in: int
    tokens_out: int
    cost: Decimal
    finish_reason: str
    request_id: str
    created_at: datetime
    upstream_model: str | None = None
    tokens_estimated: bool = False
    # assigned when the record is built, so a retried insert dedupes on it
    id: UUID = field(default_factory=uuid7)

    @classmethod
    def create(
        cls,
        api_key_id: UUID,
        provider: ProviderEnum,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost: Decimal,
        finish_reason: str,
        request_id: str,
        upstream_model: str | None = None,
        tokens_estimated: bool = False,
    ) -> "UsageRecord":
        
        if tokens_in < 0 or tokens_out < 0:
            raise InvalidTokenQuantity()
        
        if finish_reason not in VALID_FINISH_REASONS:
            raise InvalidFinishReason()
        
        if cost < 0:
            raise InvalidUsageCost()

        return cls(
            api_key_id=api_key_id,
            provider=provider,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            finish_reason=finish_reason,
            request_id=request_id,
            created_at=datetime.now(timezone.utc),
            upstream_model=upstream_model,
            tokens_estimated=tokens_estimated,
        )