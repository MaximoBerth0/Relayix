"""UsageService + UsageRepo wired together against a real DB: aggregation and
window/pagination filtering over seeded usage history (no HTTP, no Redis).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fixtures.factories import make_api_key, make_usage_record

from app.models.domain.enums import ProviderEnum
from app.repositories.usage_repo import UsageRepo
from app.services.usage_service import UsageService

OPENAI = ProviderEnum.OPENAI
ANTHROPIC = ProviderEnum.ANTHROPIC
NOW = datetime(2024, 6, 1, tzinfo=UTC)


async def seed(db_session, *records) -> None:
    db_session.add_all(records)
    await db_session.flush()


def service(db_session) -> UsageService:
    return UsageService(UsageRepo(db_session))


async def test_summary_aggregates_requests_tokens_and_cost(db_session, seed_api_key):
    await seed(
        db_session,
        make_usage_record(api_key_id=seed_api_key.id, tokens_in=100, tokens_out=50, cost=Decimal("0.01")),
        make_usage_record(api_key_id=seed_api_key.id, tokens_in=200, tokens_out=75, cost=Decimal("0.02")),
    )

    summary = await service(db_session).usage_summary(seed_api_key.id)

    assert summary.total_requests == 2
    assert summary.total_tokens_in == 300
    assert summary.total_tokens_out == 125
    assert summary.total_cost == Decimal("0.03")


async def test_summary_for_an_api_key_with_no_records_is_zero_not_none(db_session, seed_api_key):
    summary = await service(db_session).usage_summary(seed_api_key.id)

    assert summary.total_requests == 0
    assert summary.total_tokens_in == 0
    assert summary.total_tokens_out == 0
    assert summary.total_cost == Decimal("0")


async def test_summary_is_scoped_to_the_given_api_key(db_session, seed_api_key):
    other_key = make_api_key(token="other-token", name="other-key")
    db_session.add(other_key)
    await db_session.flush()

    await seed(
        db_session,
        make_usage_record(api_key_id=seed_api_key.id, cost=Decimal("0.01")),
        make_usage_record(api_key_id=other_key.id, cost=Decimal("99.00")),
    )

    summary = await service(db_session).usage_summary(seed_api_key.id)

    assert summary.total_requests == 1
    assert summary.total_cost == Decimal("0.01")


async def test_summary_excludes_records_outside_the_time_window(db_session, seed_api_key):
    await seed(
        db_session,
        make_usage_record(
            api_key_id=seed_api_key.id, created_at=NOW - timedelta(days=10), cost=Decimal("1")
        ),
        make_usage_record(api_key_id=seed_api_key.id, created_at=NOW, cost=Decimal("2")),
        make_usage_record(
            api_key_id=seed_api_key.id, created_at=NOW + timedelta(days=10), cost=Decimal("3")
        ),
    )

    summary = await service(db_session).usage_summary(
        seed_api_key.id, since=NOW - timedelta(days=1), until=NOW + timedelta(days=1)
    )

    assert summary.total_requests == 1
    assert summary.total_cost == Decimal("2")


async def test_list_usage_records_orders_most_recent_first(db_session, seed_api_key):
    older = make_usage_record(
        api_key_id=seed_api_key.id, created_at=NOW - timedelta(hours=1), request_id="older"
    )
    newer = make_usage_record(api_key_id=seed_api_key.id, created_at=NOW, request_id="newer")
    await seed(db_session, older, newer)

    records = await service(db_session).list_usage_records(seed_api_key.id)

    assert [r.request_id for r in records] == ["newer", "older"]


async def test_list_usage_records_respects_limit_and_offset(db_session, seed_api_key):
    for i in range(5):
        await seed(
            db_session,
            make_usage_record(
                api_key_id=seed_api_key.id,
                created_at=NOW - timedelta(minutes=i),
                request_id=f"req-{i}",
            ),
        )

    page = await service(db_session).list_usage_records(seed_api_key.id, limit=2, offset=1)

    assert [r.request_id for r in page] == ["req-1", "req-2"]


async def test_usage_by_model_aggregates_per_provider_and_model(db_session, seed_api_key):
    await seed(
        db_session,
        make_usage_record(api_key_id=seed_api_key.id, provider=OPENAI, model="gpt-4o", cost=Decimal("0.01")),
        make_usage_record(api_key_id=seed_api_key.id, provider=OPENAI, model="gpt-4o", cost=Decimal("0.02")),
        make_usage_record(
            api_key_id=seed_api_key.id,
            provider=ANTHROPIC,
            model="claude-sonnet-5",
            cost=Decimal("0.05"),
        ),
    )

    breakdown = await service(db_session).usage_by_model(seed_api_key.id)

    by_model = {(b.provider, b.model): b for b in breakdown}
    assert by_model[(ANTHROPIC, "claude-sonnet-5")].total_cost == Decimal("0.05")
    assert by_model[(OPENAI, "gpt-4o")].total_requests == 2
    assert by_model[(OPENAI, "gpt-4o")].total_cost == Decimal("0.03")


async def test_usage_by_model_orders_by_total_cost_descending(db_session, seed_api_key):
    await seed(
        db_session,
        make_usage_record(
            api_key_id=seed_api_key.id, provider=OPENAI, model="gpt-4o-mini", cost=Decimal("0.01")
        ),
        make_usage_record(
            api_key_id=seed_api_key.id,
            provider=ANTHROPIC,
            model="claude-sonnet-5",
            cost=Decimal("0.05"),
        ),
    )

    breakdown = await service(db_session).usage_by_model(seed_api_key.id)

    assert [b.model for b in breakdown] == ["claude-sonnet-5", "gpt-4o-mini"]
