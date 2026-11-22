"""GatewayService: routing, per-candidate adapter dispatch, breaker-facing
failover policy, and usage billing, wired together against a real DB (no
HTTP layer; provider calls are stubbed).
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from fixtures.adapters import FailingAdapter, InterruptingAdapter, StubAdapter
from sqlalchemy import select

from app.core.accounting.pricing import build_pricing_table
from app.core.accounting.usage_recorder import UsageRecorder
from app.core.adapters.registry import AdapterRegistry
from app.core.exceptions import (
    UpstreamAmbiguous,
    UpstreamStreamInterrupted,
    UpstreamUnavailable,
)
from app.core.routing.router import build_router
from app.core.routing.strategies import Candidate
from app.infra.global_exceptions import ProviderNotAvailable
from app.models.db.usage_record import Usage_Record
from app.models.domain.chat import ChatRequest, ChatResponse, Message
from app.models.domain.enums import FailoverPolicy, ProviderEnum
from app.repositories.pricing_repo import load_pricing_rates
from app.repositories.usage_repo import UsageRepo
from app.services.gateway_service import GatewayService

OPENAI = ProviderEnum.OPENAI
ANTHROPIC = ProviderEnum.ANTHROPIC

# both candidates resolve to models seed_pricing has rates for, so
# UsageRecorder can price a response instead of raising PricingRateNotFound.
CATALOG = {"default": [Candidate(OPENAI, "gpt-4o"), Candidate(ANTHROPIC, "claude-sonnet-5")]}


async def build_gateway(db_session) -> tuple[GatewayService, AdapterRegistry]:
    pricing = build_pricing_table(await load_pricing_rates(db_session))
    router = build_router(catalog=CATALOG)
    registry = AdapterRegistry()
    recorder = UsageRecorder(pricing, UsageRepo(db_session))
    return GatewayService(registry, router, recorder), registry


def request(failover_policy: FailoverPolicy = FailoverPolicy.AT_MOST_ONCE) -> ChatRequest:
    return ChatRequest(
        model="default",
        messages=[Message(role="user", content="hi")],
        failover_policy=failover_policy,
    )


async def test_a_winning_candidate_bills_exactly_once(db_session, seed_pricing, seed_api_key, usage_count):
    gateway, registry = await build_gateway(db_session)
    registry.register(OPENAI, StubAdapter(OPENAI))

    response = await gateway.complete(request(), seed_api_key.id)

    assert response.provider is OPENAI
    assert await usage_count() == 1


async def test_an_unavailable_candidate_fails_over_to_the_next(
    db_session, seed_pricing, seed_api_key, usage_count
):
    gateway, registry = await build_gateway(db_session)
    registry.register(OPENAI, FailingAdapter(UpstreamUnavailable("down")))
    registry.register(ANTHROPIC, StubAdapter(ANTHROPIC))

    response = await gateway.complete(request(), seed_api_key.id)

    assert response.provider is ANTHROPIC
    assert await usage_count() == 1


async def test_a_candidate_missing_from_the_registry_is_skipped(
    db_session, seed_pricing, seed_api_key, usage_count
):
    """The tier lists OpenAI and Anthropic, but this deployment only has an
    Anthropic adapter configured. Failover must skip past the gap rather
    than surfacing AdapterNotRegistered to the caller.
    """
    gateway, registry = await build_gateway(db_session)
    registry.register(ANTHROPIC, StubAdapter(ANTHROPIC))

    response = await gateway.complete(request(), seed_api_key.id)

    assert response.provider is ANTHROPIC
    assert await usage_count() == 1


async def test_at_most_once_ambiguous_failure_does_not_fail_over_or_bill(
    db_session, seed_pricing, seed_api_key, usage_count
):
    gateway, registry = await build_gateway(db_session)
    registry.register(OPENAI, FailingAdapter(UpstreamAmbiguous("unknown outcome")))
    registry.register(ANTHROPIC, StubAdapter(ANTHROPIC))

    with pytest.raises(UpstreamAmbiguous):
        await gateway.complete(request(FailoverPolicy.AT_MOST_ONCE), seed_api_key.id)

    assert await usage_count() == 0


async def test_at_least_once_ambiguous_failure_fails_over_and_bills_the_winner(
    db_session, seed_pricing, seed_api_key, usage_count
):
    gateway, registry = await build_gateway(db_session)
    registry.register(OPENAI, FailingAdapter(UpstreamAmbiguous("unknown outcome")))
    registry.register(ANTHROPIC, StubAdapter(ANTHROPIC))

    response = await gateway.complete(request(FailoverPolicy.AT_LEAST_ONCE), seed_api_key.id)

    assert response.provider is ANTHROPIC
    assert await usage_count() == 1


async def test_every_candidate_failing_raises_provider_not_available_and_bills_nothing(
    db_session, seed_pricing, seed_api_key, usage_count
):
    gateway, registry = await build_gateway(db_session)
    registry.register(OPENAI, FailingAdapter(UpstreamUnavailable("down")))
    registry.register(ANTHROPIC, FailingAdapter(UpstreamUnavailable("also down")))

    with pytest.raises(ProviderNotAvailable):
        await gateway.complete(request(), seed_api_key.id)

    assert await usage_count() == 0


# streaming


async def test_stream_bills_once_at_the_terminal_response(
    db_session, seed_pricing, seed_api_key, usage_count
):
    gateway, registry = await build_gateway(db_session)
    registry.register(OPENAI, StubAdapter(OPENAI, content="hello world"))

    events = [event async for event in await gateway.stream(request(), seed_api_key.id)]

    assert isinstance(events[-1], ChatResponse)
    assert await usage_count() == 1


async def test_a_stream_interrupted_after_partial_output_bills_the_partial_usage(
    db_session, seed_pricing, seed_api_key, usage_count
):
    """Once content has reached the caller, GatewayService can no longer fail
    over. It must salvage whatever partial usage the adapter attached to the
    interruption and bill it.
    """
    partial = ChatResponse(
        provider=OPENAI,
        model="gpt-4o",
        content="par",
        tokens_in=5,
        tokens_out=1,
        finish_reason="error",
        request_id="partial",
    )
    gateway, registry = await build_gateway(db_session)
    registry.register(
        OPENAI,
        InterruptingAdapter(OPENAI, UpstreamStreamInterrupted("broke", partial=partial)),
    )

    stream = await gateway.stream(request(), seed_api_key.id)
    events = []
    with pytest.raises(UpstreamStreamInterrupted):
        async for event in stream:
            events.append(event)

    assert [e.content for e in events] == ["par", "tial "]
    assert await usage_count() == 1


# bookkeeping after the provider answered


async def test_usage_is_priced_by_the_catalog_model_not_the_reported_snapshot(
    db_session, seed_pricing, seed_api_key
):
    """OpenAI answers `gpt-4o` with a dated snapshot. Pricing must use the
    catalog name the gateway asked for, and keep the reported one for audit.
    """
    gateway, registry = await build_gateway(db_session)
    registry.register(OPENAI, StubAdapter(OPENAI, reported_model="gpt-4o-2024-08-06"))

    response = await gateway.complete(request(), seed_api_key.id)

    record = await db_session.scalar(select(Usage_Record))
    assert response.model == "gpt-4o-2024-08-06"
    assert record.model == "gpt-4o"
    assert record.upstream_model == "gpt-4o-2024-08-06"
    assert record.cost == Decimal("0.00011")


async def test_a_pricing_failure_after_the_provider_answered_still_returns_the_response(
    db_session, seed_api_key, usage_count
):
    """No rates loaded: pricing raises after the provider already billed. The
    caller still gets its response; the missing record is ours to reconcile.
    """
    gateway, registry = await build_gateway(db_session)
    registry.register(OPENAI, StubAdapter(OPENAI))

    response = await gateway.complete(request(), seed_api_key.id)

    assert response.provider is OPENAI
    assert await usage_count() == 0


async def test_a_failed_usage_insert_still_returns_the_response_and_keeps_the_session_usable(
    db_session, seed_pricing, usage_count
):
    """An unknown api_key_id violates the FK on insert. The savepoint rolls back
    only that insert, so the request's transaction is still usable afterwards.
    """
    gateway, registry = await build_gateway(db_session)
    registry.register(OPENAI, StubAdapter(OPENAI))

    response = await gateway.complete(request(), uuid4())

    assert response.provider is OPENAI
    assert await usage_count() == 0


async def test_a_stream_whose_usage_insert_fails_still_delivers_the_terminal_response(
    db_session, seed_pricing, usage_count
):
    gateway, registry = await build_gateway(db_session)
    registry.register(OPENAI, StubAdapter(OPENAI, content="hello world"))

    events = [event async for event in await gateway.stream(request(), uuid4())]

    assert isinstance(events[-1], ChatResponse)
    assert await usage_count() == 0
