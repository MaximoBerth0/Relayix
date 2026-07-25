"""End-to-end flow tests for POST /v1/chat/completions.

Everything on the request path is real (authentication, per-key rate limiting,
routing, the gateway service, usage recording and response serialization) over
real Postgres and Redis. The only stubbed component is the provider's outbound
HTTP call, replaced by `StubAdapter` (see conftest).
"""

from conftest import AUTH_HEADERS, FailingAdapter, StubAdapter, app
from sqlalchemy import func, select

from app.core.exceptions import UpstreamUnavailable
from app.models.db.usage_record import Usage_Record
from app.models.domain.enums import ProviderEnum


async def _usage_count(db_session) -> int:
    return await db_session.scalar(select(func.count()).select_from(Usage_Record))


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

async def test_missing_auth_is_rejected(client, stub_openai, db_session):
    """No bearer token -> 401, and nothing reaches the provider or the ledger."""
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "ping"}],
        },
    )

    assert response.status_code == 401
    assert stub_openai.calls == []
    assert await _usage_count(db_session) == 0


async def test_idempotent_replay(client, stub_openai, db_session):
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
    assert await _usage_count(db_session) == 1


async def test_failover_to_anthropic(client, stub_openai, db_session):
    """When the primary provider is provably unavailable, the gateway fails over
    to the next candidate and bills that provider instead.
    """
    # replace the OpenAI stub with one that always fails; wire an Anthropic stub.
    openai = FailingAdapter(UpstreamUnavailable("openai down"))
    anthropic = StubAdapter(ProviderEnum.ANTHROPIC)
    app.state.registry.register(ProviderEnum.OPENAI, openai)
    app.state.registry.register(ProviderEnum.ANTHROPIC, anthropic)

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
    # 'default' tries OpenAI first (it failed), then Anthropic's claude-sonnet-5.
    assert body["provider"] == "anthropic"
    assert body["model"] == "claude-sonnet-5"

    # OpenAI was attempted once, then Anthropic served the request.
    assert len(openai.calls) == 1
    assert len(anthropic.calls) == 1

    # exactly one usage record, priced under Anthropic
    record = await db_session.scalar(select(Usage_Record))
    assert record is not None
    assert record.provider == "anthropic"
    assert record.model == "claude-sonnet-5"


async def test_rate_limit_returns_429(client, stub_openai, seed_api_key, db_session):
    """A key limited to 1 rpm gets its first request through and the second 429'd,
    and the throttled request never reaches the provider or the ledger.
    """
    # tighten this key's limit so the second call in the same window is rejected.
    seed_api_key.rate_limit_rpm = 1
    await db_session.flush()

    payload = {
        "model": "default",
        "messages": [{"role": "user", "content": "ping"}],
    }

    first = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=payload)
    second = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=payload)

    assert first.status_code == 200
    assert second.status_code == 429
    body = second.json()
    assert body["error_code"] == "RATE_LIMITED"
    assert "retry_after_s" in body
    assert "Retry-After" in second.headers

    # only the allowed request reached the provider and was billed.
    assert len(stub_openai.calls) == 1
    assert await _usage_count(db_session) == 1
