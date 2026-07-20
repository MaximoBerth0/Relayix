from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class UsageSummary:
    """Aggregate token and cost totals for one api key over a time window"""

    total_requests: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost: Decimal
    since: datetime | None = None
    until: datetime | None = None
