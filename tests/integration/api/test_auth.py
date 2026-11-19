"""End-to-end authentication behaviour on POST /v1/chat/completions."""


async def test_missing_auth_is_rejected(client, stub_openai, usage_count):
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
    assert await usage_count() == 0
