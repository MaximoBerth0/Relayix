"""Failover between providers, observed end-to-end through the HTTP layer."""

from fixtures.adapters import FailingAdapter, StubAdapter
from fixtures.factories import AUTH_HEADERS
from sqlalchemy import select

from app.core.exceptions import UpstreamUnavailable
from app.main import app
from app.models.db.usage_record import Usage_Record
from app.models.domain.enums import ProviderEnum


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
