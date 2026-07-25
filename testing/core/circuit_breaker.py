from conftest import AUTH_HEADERS


async def test_circuit_breaker_working(client, stub_openai, db_session):
    response = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "ping"}],
        },
    )

    assert response.status_code == 
    body = response.json()
    # 'default' tier prefers OpenAI gpt-4o, which the stub serves.
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o"
    assert body["content"] == "stubbed reply"
    assert body["tokens_in"] == 12
    assert body["tokens_out"] == 8

    # the provider 
    assert len(stub_openai.calls) == 1
