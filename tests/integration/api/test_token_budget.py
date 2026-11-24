"""Token budget per key: tokens per minute and the monthly quota, end-to-end.

The stub provider reports 12 input + 8 output tokens per call (20 real tokens),
while a one-word prompt estimates to a handful at admission, so these tests also
prove the admission estimate is corrected with the real count afterwards.
"""

from datetime import UTC, datetime, timedelta

from fixtures.factories import AUTH_HEADERS, make_usage_record

from app.infra.quota.redis_quota import RedisTokenQuota

PAYLOAD = {"model": "default", "messages": [{"role": "user", "content": "ping"}]}
REAL_TOKENS = 20


def month_key(api_key_id) -> str:
    return f"quota:{api_key_id}:{datetime.now(UTC):%Y-%m}"


async def test_tokens_per_minute_counts_the_real_usage_not_just_the_estimate(
    client, stub_openai, seed_api_key, db_session
):
    """25 tpm: the first call is admitted on its estimate, then corrected to 20
    real tokens, which leaves too few for the second call's estimate.
    """
    seed_api_key.rate_limit_tpm = 25
    await db_session.flush()

    first = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=PAYLOAD)
    second = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=PAYLOAD)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error_code"] == "RATE_LIMITED"
    assert "Retry-After" in second.headers
    assert len(stub_openai.calls) == 1


async def test_monthly_quota_rejects_once_spent(client, stub_openai, seed_api_key, db_session):
    seed_api_key.monthly_token_quota = 2 * REAL_TOKENS + 5
    await db_session.flush()

    responses = [
        await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=PAYLOAD) for _ in range(3)
    ]

    assert [r.status_code for r in responses] == [200, 200, 429]
    body = responses[2].json()
    assert body["error_code"] == "TOKEN_QUOTA_EXCEEDED"
    # points at the start of next month
    assert 0 < body["retry_after_s"] <= 31 * 86_400
    assert "Retry-After" in responses[2].headers
    assert len(stub_openai.calls) == 2


async def test_monthly_counter_tracks_real_tokens(
    client, stub_openai, seed_api_key, db_session, redis_client
):
    seed_api_key.monthly_token_quota = 1_000
    await db_session.flush()

    await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=PAYLOAD)
    await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=PAYLOAD)

    assert int(await redis_client.get(month_key(seed_api_key.id))) == 2 * REAL_TOKENS


async def test_a_missing_counter_is_seeded_from_this_months_usage(
    client, stub_openai, seed_api_key, db_session, redis_client
):
    """Redis lost the counter (restart, eviction): the month's usage in Postgres
    still counts, while last month's does not.
    """
    seed_api_key.monthly_token_quota = 1_000
    now = datetime.now(UTC)
    db_session.add_all(
        [
            make_usage_record(api_key_id=seed_api_key.id, tokens_in=600, tokens_out=400, created_at=now),
            make_usage_record(
                api_key_id=seed_api_key.id,
                tokens_in=5_000,
                tokens_out=5_000,
                created_at=now.replace(day=1) - timedelta(days=1),
            ),
        ]
    )
    await db_session.flush()

    response = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=PAYLOAD)

    assert response.status_code == 429
    assert response.json()["error_code"] == "TOKEN_QUOTA_EXCEEDED"
    assert int(await redis_client.get(month_key(seed_api_key.id))) == 1_000
    assert stub_openai.calls == []


async def test_a_key_without_quota_keeps_no_counter(client, stub_openai, seed_api_key, redis_client):
    response = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=PAYLOAD)

    assert response.status_code == 200
    assert await redis_client.get(month_key(seed_api_key.id)) is None


async def test_quota_store_down_lets_the_request_through(
    client, stub_openai, seed_api_key, db_session, monkeypatch
):
    """A budget, not a security boundary: Redis being down must not take the gateway down."""
    from app.core.exceptions import RateLimiterUnavailable

    async def unavailable(*args, **kwargs):
        raise RateLimiterUnavailable()

    monkeypatch.setattr(RedisTokenQuota, "used", unavailable)
    monkeypatch.setattr(RedisTokenQuota, "add", unavailable)
    seed_api_key.monthly_token_quota = 1
    await db_session.flush()

    response = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=PAYLOAD)

    assert response.status_code == 200
    assert len(stub_openai.calls) == 1


async def test_streamed_requests_are_settled_too(client, stub_openai, seed_api_key, db_session, redis_client):
    seed_api_key.monthly_token_quota = 1_000
    await db_session.flush()

    response = await client.post(
        "/v1/chat/completions", headers=AUTH_HEADERS, json={**PAYLOAD, "stream": True}
    )

    assert response.status_code == 200
    assert "event: done" in response.text
    assert int(await redis_client.get(month_key(seed_api_key.id))) == REAL_TOKENS
