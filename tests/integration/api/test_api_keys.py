"""End-to-end behaviour of the api-key management endpoints."""

from fixtures.factories import ADMIN_AUTH_HEADERS, AUTH_HEADERS


async def test_create_api_key_returns_the_raw_token_once(client):
    response = await client.post(
        "/v1/api-keys",
        headers=ADMIN_AUTH_HEADERS,
        json={"name": "my-key"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "my-key"
    assert body["is_active"] is True
    assert body["api_key"].startswith("rlx_")


async def test_created_key_authenticates_chat_requests(client, stub_openai):
    created = await client.post(
        "/v1/api-keys",
        headers=ADMIN_AUTH_HEADERS,
        json={"name": "my-key"},
    )
    token = created.json()["api_key"]

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "default", "messages": [{"role": "user", "content": "ping"}]},
    )

    assert response.status_code == 200


async def test_create_rejects_blank_name(client):
    response = await client.post(
        "/v1/api-keys",
        headers=ADMIN_AUTH_HEADERS,
        json={"name": ""},
    )

    assert response.status_code == 422


async def test_create_rejects_non_positive_rate_limit(client):
    response = await client.post(
        "/v1/api-keys",
        headers=ADMIN_AUTH_HEADERS,
        json={"name": "my-key", "rate_limit_rpm": 0},
    )

    assert response.status_code == 422


async def test_list_returns_every_key_most_recent_first(client):
    await client.post("/v1/api-keys", headers=ADMIN_AUTH_HEADERS, json={"name": "first"})
    await client.post("/v1/api-keys", headers=ADMIN_AUTH_HEADERS, json={"name": "second"})

    response = await client.get("/v1/api-keys", headers=ADMIN_AUTH_HEADERS)

    assert response.status_code == 200
    names = [entry["name"] for entry in response.json()]
    assert names == ["second", "first"]


async def test_list_never_returns_the_key_hash_or_token(client):
    await client.post("/v1/api-keys", headers=ADMIN_AUTH_HEADERS, json={"name": "my-key"})

    response = await client.get("/v1/api-keys", headers=ADMIN_AUTH_HEADERS)

    entry = response.json()[0]
    assert "key_hash" not in entry
    assert "api_key" not in entry


async def test_revoke_deactivates_the_key(client):
    created = await client.post("/v1/api-keys", headers=ADMIN_AUTH_HEADERS, json={"name": "my-key"})
    key_id = created.json()["id"]

    response = await client.post(f"/v1/api-keys/{key_id}/revoke", headers=ADMIN_AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["is_active"] is False


async def test_revoked_key_can_no_longer_authenticate(client):
    created = await client.post("/v1/api-keys", headers=ADMIN_AUTH_HEADERS, json={"name": "my-key"})
    body = created.json()
    token = body["api_key"]
    key_id = body["id"]

    await client.post(f"/v1/api-keys/{key_id}/revoke", headers=ADMIN_AUTH_HEADERS)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "default", "messages": [{"role": "user", "content": "ping"}]},
    )

    assert response.status_code == 401


async def test_revoke_unknown_key_returns_404(client):
    response = await client.post(
        "/v1/api-keys/018f9a1e-0000-7000-8000-000000000000/revoke",
        headers=ADMIN_AUTH_HEADERS,
    )

    assert response.status_code == 404


async def test_management_endpoints_reject_missing_admin_auth(client):
    create = await client.post("/v1/api-keys", json={"name": "my-key"})
    listing = await client.get("/v1/api-keys")

    assert create.status_code == 401
    assert listing.status_code == 401


async def test_management_endpoints_reject_a_regular_api_key(client, seed_api_key):
    response = await client.get("/v1/api-keys", headers=AUTH_HEADERS)

    assert response.status_code == 401
