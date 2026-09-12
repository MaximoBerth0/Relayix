from dataclasses import dataclass
from decimal import Decimal

from app.models.domain.enums import ProviderEnum


@dataclass(frozen=True)
class UsageByModel:
    """Aggregate token and cost totals for one provider/model pair."""

    provider: ProviderEnum
    model: str
    total_requests: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost: Decimal
