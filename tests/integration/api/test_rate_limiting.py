"""Per-key rate limiting, observed end-to-end through the HTTP layer."""

from fixtures.factories import ADMIN_AUTH_HEADERS, AUTH_HEADERS

from app.infra.config import settings
from app.main import app


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


async def test_usage_endpoints_have_their_own_bucket(client, seed_api_key, monkeypatch):
    """/v1/usage is throttled per key at a fixed rpm, apart from the completions bucket."""
    monkeypatch.setattr(settings, "usage_rate_limit_rpm", 2)

    assert (await client.get("/v1/usage", headers=AUTH_HEADERS)).status_code == 200
    assert (await client.get("/v1/usage/by-model", headers=AUTH_HEADERS)).status_code == 200
    third = await client.get("/v1/usage/records", headers=AUTH_HEADERS)

    assert third.status_code == 429
    assert third.json()["error_code"] == "RATE_LIMITED"
    assert "Retry-After" in third.headers

    # the completions bucket was never touched
    await app.state.rate_limiter.check(seed_api_key.id, 1)


async def test_admin_endpoints_are_throttled(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_rate_limit_rpm", 1)

    first = await client.get("/v1/api-keys", headers=ADMIN_AUTH_HEADERS)
    second = await client.get("/v1/api-keys", headers=ADMIN_AUTH_HEADERS)

    assert first.status_code == 200
    assert second.status_code == 429


async def test_admin_bucket_is_not_spent_by_failed_auth(client, monkeypatch):
    """a wrong admin token is a 401 before the bucket, so it can't lock the admin out"""
    monkeypatch.setattr(settings, "admin_rate_limit_rpm", 1)

    bad = {"Authorization": "Bearer wrong"}
    for _ in range(3):
        assert (await client.get("/v1/api-keys", headers=bad)).status_code == 401

    assert (await client.get("/v1/api-keys", headers=ADMIN_AUTH_HEADERS)).status_code == 200
