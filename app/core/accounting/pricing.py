from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from app.models.domain.enums import ProviderEnum
from app.models.domain.pricing_rate import PricingRate


class PricingTable:
    """Holds the pricing rates per (provider, model) and computes request cost.
    """

    def __init__(self) -> None:
        self._rates: dict[tuple[ProviderEnum, str], list[PricingRate]] = {}

    def register(self, rate: PricingRate) -> None:
        """Add a rate to the history for its (provider, model) pair."""
        self._rates.setdefault((rate.provider, rate.model), []).append(rate)

    def get(
        self,
        provider: ProviderEnum,
        model: str,
        as_of: datetime | None = None,
    ) -> PricingRate:
        """Return the rate in effect for (provider, model) at `as_of` (default: now)."""
        as_of = as_of or datetime.now(timezone.utc)

        history = self._rates.get((provider, model))
        effective = [rate for rate in history or [] if rate.effective_from <= as_of]
        if not effective:
            raise KeyError(
                f"no pricing rate in effect for provider {provider!r} "
                f"model {model!r} as of {as_of.isoformat()}"
            )

        return max(effective, key=lambda rate: rate.effective_from)

    def cost_for(
        self,
        provider: ProviderEnum,
        model: str,
        tokens_in: int,
        tokens_out: int,
        as_of: datetime | None = None,
    ) -> Decimal:
        """Return the cost of a request given its provider, model and token counts."""
        return self.get(provider, model, as_of).calculate_cost(tokens_in, tokens_out)


def build_pricing_table(rates: Iterable[PricingRate]) -> PricingTable:
    """Construct a pricing table from rates loaded out of the pricing repository."""
    table = PricingTable()
    for rate in rates:
        table.register(rate)
    return table
