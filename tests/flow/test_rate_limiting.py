"""Per-key rate limiting, observed end-to-end through the HTTP layer."""

from fixtures.factories import AUTH_HEADERS


async def test_rate_limit_returns_429(client, stub_openai, seed_api_key, db_session, usage_count):
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
    assert await usage_count() == 1
