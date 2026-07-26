"""End-to-end flow tests for POST /v1/chat/completions.

Everything on the request path is real (authentication, per-key rate limiting,
routing, the gateway service, usage recording and response serialization) over
real Postgres and Redis. The only stubbed component is the provider's outbound
HTTP call, replaced by `StubAdapter` (see fixtures/adapters.py).
"""

from fixtures.factories import AUTH_HEADERS
from sqlalchemy import select

from app.models.db.usage_record import Usage_Record


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
