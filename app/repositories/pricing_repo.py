from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.pricing import Pricing
from app.models.domain.enums import ProviderEnum
from app.models.domain.pricing_rate import PricingRate


async def load_pricing_rates(session: AsyncSession) -> list[PricingRate]:
    """Load every pricing row and map it to a PricingRate domain object."""
    rows = await session.scalars(select(Pricing))
    return [
        PricingRate.create(
            provider=ProviderEnum(row.provider),
            model=row.model,
            price_per_1k_input=row.price_per_1k_input_tokens,
            price_per_1k_output=row.price_per_1k_output_tokens,
            effective_from=row.effective_from,
        )
        for row in rows
    ]
