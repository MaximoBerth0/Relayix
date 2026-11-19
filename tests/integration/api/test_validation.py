"""Payload validation on POST /v1/chat/completions.

A malformed request must be rejected by the transport schema before it reaches
the gateway: no provider call, no usage record. 
"""

import pytest
from fixtures.factories import AUTH_HEADERS

VALID_MESSAGES = [{"role": "user", "content": "ping"}]


def _locations(response) -> list[tuple]:
    """The `loc` of every validation error, as tuples for easy comparison."""
    return [tuple(error["loc"]) for error in response.json()["detail"]]


@pytest.mark.parametrize(
    ("payload", "expected_loc"),
    [
        pytest.param(
            {"messages": VALID_MESSAGES},
            ("body", "model"),
            id="model-missing",
        ),
        pytest.param(
            {"model": "", "messages": VALID_MESSAGES},
            ("body", "model"),
            id="model-empty",
        ),
        pytest.param(
            {"model": "default"},
            ("body", "messages"),
            id="messages-missing",
        ),
        pytest.param(
            {"model": "default", "messages": []},
            ("body", "messages"),
            id="messages-empty",
        ),
        pytest.param(
            {"model": "default", "messages": [{"role": "user", "content": ""}]},
            ("body", "messages", 0, "content"),
            id="content-empty",
        ),
        pytest.param(
            {"model": "default", "messages": [{"role": "user"}]},
            ("body", "messages", 0, "content"),
            id="content-missing",
        ),
        pytest.param(
            {"model": "default", "messages": [{"role": "root", "content": "ping"}]},
            ("body", "messages", 0, "role"),
            id="role-unknown",
        ),
        pytest.param(
            {"model": "default", "messages": VALID_MESSAGES, "max_tokens": 0},
            ("body", "max_tokens"),
            id="max-tokens-not-positive",
        ),
        pytest.param(
            {"model": "default", "messages": VALID_MESSAGES, "failover_policy": "always"},
            ("body", "failover_policy"),
            id="failover-policy-unknown",
        ),
    ],
)
async def test_invalid_payload_returns_422(client, stub_openai, usage_count, payload, expected_loc):
    """An authenticated request with a bad body is rejected at the schema."""
    response = await client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=payload)

    assert response.status_code == 422
    assert expected_loc in _locations(response)

    # rejected before the gateway: nothing was called and nothing was billed.
    assert stub_openai.calls == []
    assert await usage_count() == 0


async def test_valid_boundary_values_are_accepted(client, stub_openai):
    """The edges of the accepted ranges are inside them, not outside."""
    response = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "model": "default",
            "messages": VALID_MESSAGES,
            "max_tokens": 1,
            "failover_policy": "at_least_once",
        },
    )

    assert response.status_code == 200
    assert len(stub_openai.calls) == 1


async def test_unauthenticated_invalid_payload_is_rejected_as_401(client, stub_openai, usage_count):
    """Auth runs before body validation, so a bad payload from an anonymous
    caller gets a 401 and never learns which fields the schema wants.
    """
    response = await client.post("/v1/chat/completions", json={"model": ""})

    assert response.status_code == 401
    assert "detail" not in response.json()
    assert stub_openai.calls == []
    assert await usage_count() == 0
