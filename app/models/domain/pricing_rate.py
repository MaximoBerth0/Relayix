from dataclasses import dataclass
from decimal import Decimal 
from datetime import datetime 
from app.models.domain.enums import ProviderEnum
from app.models.domain.exceptions import InvalidModelName, InvalidTokenPrice, InvalidTokenQuantity

@dataclass(frozen=True)
class PricingRate:
    provider: str
    model: str
    price_per_1k_input: Decimal
    price_per_1k_output: Decimal
    effective_from: datetime

    @classmethod
    def create(
        cls, 
        provider: ProviderEnum,
        model: str,
        price_per_1k_input: Decimal,
        price_per_1k_output: Decimal,
        effective_from: datetime,   
    ) -> "PricingRate":
        
        if not model.strip():
            raise InvalidModelName()
        
        if price_per_1k_input < 0 or price_per_1k_output < 0:
            raise InvalidTokenPrice()
        
        return cls(
            provider=provider,
            model=model,
            price_per_1k_input=price_per_1k_input,
            price_per_1k_output=price_per_1k_output,
            effective_from=effective_from,
        )
    
    def calculate_cost(self, tokens_in: int, tokens_out: int) -> Decimal:

        if tokens_in < 0 or tokens_out < 0:
            raise InvalidTokenQuantity()

        cost_in = (Decimal(tokens_in) / 1000) * self.price_per_1k_input
        cost_out = (Decimal(tokens_out) / 1000) * self.price_per_1k_output
        return cost_in + cost_out
