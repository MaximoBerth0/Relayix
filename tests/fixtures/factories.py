"""Builders for the ORM rows a test needs seeded.

Plain constructors, no session and no I/O: the fixtures in conftest own the
insert so a test can also build a row and persist it itself.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.infra.security.crypto import hash_api_key
from app.models.db.api_key import Api_Key
from app.models.db.pricing import Pricing
from app.models.domain.enums import ProviderEnum

# plaintext bearer token whose sha256 is seeded into the api_key table.
API_TOKEN = "relayix-test-token"
AUTH_HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}


def make_api_key(token: str = API_TOKEN, *, name: str = "test-key", is_active: bool = True) -> Api_Key:
    """An API key row whose bearer token is `token`."""
    return Api_Key(
        name=name,
        key_hash=hash_api_key(token),
        is_active=is_active,
    )


def make_pricing_rows() -> list[Pricing]:
    """Pricing for the models the default catalog can route to, so UsageRecorder
    can price a response instead of raising PricingRateNotFound.
    """
    return [
        Pricing(
            provider=ProviderEnum.OPENAI.value,
            model="gpt-4o",
            price_per_1k_input_tokens=Decimal("0.0025"),
            price_per_1k_output_tokens=Decimal("0.010"),
            effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        ),
        Pricing(
            provider=ProviderEnum.ANTHROPIC.value,
            model="claude-sonnet-5",
            price_per_1k_input_tokens=Decimal("0.003"),
            price_per_1k_output_tokens=Decimal("0.015"),
            effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        ),
    ]
