"""PricingTable and PricingRate: the money-critical cost calculation that
every billed request runs through.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.accounting.pricing import PricingTable, build_pricing_table
from app.core.exceptions import PricingRateNotFound
from app.models.domain.enums import ProviderEnum
from app.models.domain.exceptions import InvalidModelName, InvalidTokenPrice, InvalidTokenQuantity
from app.models.domain.pricing_rate import PricingRate

OPENAI = ProviderEnum.OPENAI
EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def make_rate(
    *,
    model: str = "gpt-4o",
    price_in: str = "0.0025",
    price_out: str = "0.010",
    effective_from: datetime = EPOCH,
) -> PricingRate:
    return PricingRate.create(
        provider=OPENAI,
        model=model,
        price_per_1k_input=Decimal(price_in),
        price_per_1k_output=Decimal(price_out),
        effective_from=effective_from,
    )


# PricingRate.create validation


def test_create_rejects_a_blank_model_name():
    with pytest.raises(InvalidModelName):
        PricingRate.create(
            provider=OPENAI,
            model="   ",
            price_per_1k_input=Decimal("0.001"),
            price_per_1k_output=Decimal("0.001"),
            effective_from=EPOCH,
        )


def test_create_rejects_a_negative_input_price():
    with pytest.raises(InvalidTokenPrice):
        make_rate(price_in="-0.001")


def test_create_rejects_a_negative_output_price():
    with pytest.raises(InvalidTokenPrice):
        make_rate(price_out="-0.001")


def test_create_accepts_a_zero_price():
    make_rate(price_in="0", price_out="0")


# PricingRate.calculate_cost


def test_calculate_cost_sums_input_and_output():
    rate = make_rate(price_in="0.0025", price_out="0.010")

    cost = rate.calculate_cost(tokens_in=1000, tokens_out=1000)

    assert cost == Decimal("0.0125")


def test_calculate_cost_scales_sub_1k_token_counts():
    rate = make_rate(price_in="1.000", price_out="0")

    assert rate.calculate_cost(tokens_in=500, tokens_out=0) == Decimal("0.500")


def test_calculate_cost_of_zero_tokens_is_zero():
    rate = make_rate()

    assert rate.calculate_cost(tokens_in=0, tokens_out=0) == Decimal("0")


def test_calculate_cost_rejects_negative_token_counts():
    rate = make_rate()

    with pytest.raises(InvalidTokenQuantity):
        rate.calculate_cost(tokens_in=-1, tokens_out=0)
    with pytest.raises(InvalidTokenQuantity):
        rate.calculate_cost(tokens_in=0, tokens_out=-1)


# PricingTable.get / cost_for


def test_get_returns_the_only_registered_rate():
    table = PricingTable()
    rate = make_rate()
    table.register(rate)

    assert table.get(OPENAI, "gpt-4o") is rate


def test_get_raises_when_nothing_is_registered_for_the_pair():
    table = PricingTable()

    with pytest.raises(PricingRateNotFound):
        table.get(OPENAI, "gpt-4o")


def test_get_raises_when_only_a_different_model_is_registered():
    table = PricingTable()
    table.register(make_rate(model="gpt-4o"))

    with pytest.raises(PricingRateNotFound):
        table.get(OPENAI, "gpt-4o-mini")


def test_get_picks_the_most_recent_rate_in_effect_as_of_the_given_time():
    """Two rate changes over time: `as_of` between them must resolve to the
    one that was active then, not the latest one on file.
    """
    table = PricingTable()
    old = make_rate(price_in="0.001", effective_from=EPOCH)
    new = make_rate(price_in="0.002", effective_from=EPOCH + timedelta(days=365))
    table.register(old)
    table.register(new)

    resolved = table.get(OPENAI, "gpt-4o", as_of=EPOCH + timedelta(days=30))

    assert resolved is old


def test_get_resolves_to_now_when_as_of_is_omitted():
    table = PricingTable()
    table.register(make_rate(effective_from=EPOCH))

    table.get(OPENAI, "gpt-4o")  # does not raise


def test_get_raises_when_as_of_is_before_every_rate():
    table = PricingTable()
    table.register(make_rate(effective_from=EPOCH))

    with pytest.raises(PricingRateNotFound):
        table.get(OPENAI, "gpt-4o", as_of=EPOCH - timedelta(days=1))


def test_get_ignores_registration_order_and_picks_by_effective_from():
    """Registering the newer rate first must not change which one wins."""
    table = PricingTable()
    new = make_rate(price_in="0.002", effective_from=EPOCH + timedelta(days=365))
    old = make_rate(price_in="0.001", effective_from=EPOCH)
    table.register(new)
    table.register(old)

    assert table.get(OPENAI, "gpt-4o", as_of=EPOCH) is old


def test_cost_for_delegates_to_the_resolved_rate():
    table = PricingTable()
    table.register(make_rate(price_in="0.0025", price_out="0.010"))

    cost = table.cost_for(OPENAI, "gpt-4o", tokens_in=1000, tokens_out=1000)

    assert cost == Decimal("0.0125")


# build_pricing_table


def test_build_pricing_table_registers_every_rate():
    rates = [make_rate(model="gpt-4o"), make_rate(model="gpt-4o-mini", price_in="0.0001")]

    table = build_pricing_table(rates)

    assert table.get(OPENAI, "gpt-4o") is rates[0]
    assert table.get(OPENAI, "gpt-4o-mini") is rates[1]


def test_build_pricing_table_of_an_empty_iterable_resolves_nothing():
    table = build_pricing_table([])

    with pytest.raises(PricingRateNotFound):
        table.get(OPENAI, "gpt-4o")
