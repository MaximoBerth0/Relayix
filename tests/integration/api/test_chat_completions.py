"""End-to-end flow tests for POST /v1/chat/completions.

Everything on the request path is real (authentication, per-key rate limiting,
routing, the gateway service, usage recording and response serialization) over
real Postgres and Redis. The only stubbed component is the provider's outbound
HTTP call, replaced by `StubAdapter` (see fixtures/adapters.py).
"""

from fixtures.adapters import FailingAdapter
from fixtures.factories import AUTH_HEADERS
from sqlalchemy import select

from app.core.exceptions import UpstreamAmbiguous, UpstreamUnavailable
from app.main import app
from app.models.db.usage_record import Usage_Record
from app.models.domain.enums import ProviderEnum


async def test_completion_happy_path(client, stub_openai, db_session):
    """A valid request is authenticated, routed to a provider and billed once."""
    response = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "ping"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    # 'default' tier prefers OpenAI gpt-4o, which the stub serves.
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o"
    assert body["content"] == "stubbed reply"
    assert body["tokens_in"] == 12
    assert body["tokens_out"] == 8

    # the provider was hit exactly once...
    assert len(stub_openai.calls) == 1
    # ...and the tail of the flow persisted a priced usage record.
    record = await db_session.scalar(select(Usage_Record))
    assert record is not None
    assert record.provider == "openai"
    assert record.model == "gpt-4o"
    assert record.token_in == 12
    assert record.token_out == 8
    # cost = 12/1000 * 0.0025 + 8/1000 * 0.010
    assert float(record.cost) == 0.00011


async def test_idempotent_replay(client, stub_openai, usage_count):
    """Two requests with the same Idempotency-Key hit the provider once and
    return the same response, billing the caller a single time.
    """
    payload = {
        "model": "default",
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {**AUTH_HEADERS, "Idempotency-Key": "abc-123"}

    first = await client.post("/v1/chat/completions", headers=headers, json=payload)
    second = await client.post("/v1/chat/completions", headers=headers, json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    # the replay was served from the idempotency store, not re-executed.
    assert len(stub_openai.calls) == 1
    assert await usage_count() == 1


async def test_ambiguous_outcome_is_not_retried_under_the_same_key(
    client, stub_openai, usage_count
):
    """An ambiguous upstream failure parks the key instead of releasing it.
    """
    openai = FailingAdapter(UpstreamAmbiguous("openai timed out"))
    app.state.registry.register(ProviderEnum.OPENAI, openai)

    payload = {
        "model": "default",
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {**AUTH_HEADERS, "Idempotency-Key": "ambiguous-1"}

    first = await client.post("/v1/chat/completions", headers=headers, json=payload)
    second = await client.post("/v1/chat/completions", headers=headers, json=payload)

    # the original attempt surfaces the ambiguity...
    assert first.status_code == 502
    assert first.json()["error_code"] == "UPSTREAM_AMBIGUOUS"

    # ...and the retry is answered from the record, not re-executed.
    assert second.status_code == 409
    assert second.json()["error_code"] == "IDEMPOTENCY_OUTCOME_UNKNOWN"

    # the provider was attempted exactly once: no double-spend.
    assert len(openai.calls) == 1
    # nothing completed, so nothing was billed.
    assert await usage_count() == 0


async def test_exhausted_failover_preserves_an_earlier_ambiguity(
    client, stub_openai, usage_count
):
    """Ambiguity survives a failover that then fails for a different reason.

    Under AT_LEAST_ONCE the gateway fails over past an unknown outcome
    """
    openai = FailingAdapter(UpstreamAmbiguous("openai timed out"))
    anthropic = FailingAdapter(UpstreamUnavailable("anthropic refused the connection"))
    app.state.registry.register(ProviderEnum.OPENAI, openai)
    app.state.registry.register(ProviderEnum.ANTHROPIC, anthropic)

    payload = {
        "model": "default",
        "messages": [{"role": "user", "content": "ping"}],
        "failover_policy": "at_least_once",
    }
    headers = {**AUTH_HEADERS, "Idempotency-Key": "ambiguous-2"}

    first = await client.post("/v1/chat/completions", headers=headers, json=payload)
    second = await client.post("/v1/chat/completions", headers=headers, json=payload)

    # both candidates were tried, and the ambiguity outranks the clean failure.
    assert len(openai.calls) == 1
    assert len(anthropic.calls) == 1
    assert first.status_code == 502
    assert first.json()["error_code"] == "UPSTREAM_AMBIGUOUS"

    # so the key is parked, not released: the retry is refused.
    assert second.status_code == 409
    assert second.json()["error_code"] == "IDEMPOTENCY_OUTCOME_UNKNOWN"

    # neither provider was called a second time, and nothing was billed.
    assert len(openai.calls) == 1
    assert await usage_count() == 0
