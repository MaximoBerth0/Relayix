"""Builders for the ORM rows a test needs seeded.

Plain constructors, no session and no I/O: the fixtures in conftest own the
insert so a test can also build a row and persist it itself.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.infra.security.crypto import hash_api_key
from app.models.db.api_key import Api_Key
from app.models.db.pricing import Pricing
from app.models.db.usage_record import Usage_Record
from app.models.domain.enums import ProviderEnum

# plaintext bearer token whose sha256 is seeded into the api_key table.
API_TOKEN = "relayix-test-token"
AUTH_HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}

# admin master token, set on settings.admin_api_token by conftest's env block.
ADMIN_TOKEN = "relayix-test-admin-token"
ADMIN_AUTH_HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


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


def make_usage_record(
    *,
    api_key_id: UUID,
    provider: ProviderEnum = ProviderEnum.OPENAI,
    model: str = "gpt-4o",
    tokens_in: int = 100,
    tokens_out: int = 50,
    cost: Decimal = Decimal("0.01"),
    finish_reason: str = "stop",
    request_id: str | None = None,
    created_at: datetime | None = None,
) -> Usage_Record:
    """A usage_record row, for tests that seed usage history directly instead
    of going through UsageRecorder.
    """
    return Usage_Record(
        api_key_id=api_key_id,
        provider=provider.value,
        model=model,
        token_in=tokens_in,
        token_out=tokens_out,
        cost=cost,
        finish_reason=finish_reason,
        request_id=request_id or f"req-{uuid4().hex[:8]}",
        created_at=created_at or datetime.now(UTC),
    )
