"""End-to-end flow tests for POST /v1/chat/completions with `"stream": true`.

Same real path as test_chat_completions.py (auth, rate limiting, routing,
gateway, usage recording, idempotency) except the response is SSE instead of
a single buffered JSON body. StubAdapter/FailingAdapter/InterruptingAdapter
(fixtures/adapters.py) stand in for the provider's outbound HTTP call.
"""

import json

from fixtures.adapters import FailingAdapter, InterruptingAdapter, StubAdapter
from fixtures.factories import AUTH_HEADERS
from sqlalchemy import select

from app.core.exceptions import (
    UpstreamAmbiguous,
    UpstreamStreamInterrupted,
    UpstreamUnavailable,
)
from app.main import app
from app.models.db.usage_record import Usage_Record
from app.models.domain.chat import ChatResponse
from app.models.domain.enums import ProviderEnum


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """[(event_type, data_dict), ...] for an SSE response body."""
    events = []
    for block in text.strip("\n").split("\n\n"):
        if not block:
            continue
        event_type = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_type = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        events.append((event_type, data))
    return events


async def test_streaming_happy_path(client, stub_openai, db_session):
    """Deltas arrive first, then a single `done` event carrying the same shape
    as the non-streaming response; exactly one usage record is billed.
    """
    response = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    assert [e for e, _ in events[:-1]] == ["delta"] * (len(events) - 1)
    assert events[-1][0] == "done"

    reconstructed = "".join(d["content"] for _, d in events[:-1])
    done = events[-1][1]
    assert reconstructed == done["content"] == "stubbed reply"
    assert done["provider"] == "openai"
    assert done["model"] == "gpt-4o"
    assert done["tokens_in"] == 12
    assert done["tokens_out"] == 8

    assert len(stub_openai.calls) == 1
    record = await db_session.scalar(select(Usage_Record))
    assert record is not None
    assert record.provider == "openai"
    assert record.token_in == 12
    assert record.token_out == 8


async def test_streaming_failover_before_first_token(client, stub_openai, db_session):
    """A candidate that fails before producing anything fails over exactly
    like the non-streaming path, and the winner streams normally.
    """
    openai = FailingAdapter(UpstreamUnavailable("openai down"))
    anthropic = StubAdapter(ProviderEnum.ANTHROPIC, content="hello from claude")
    app.state.registry.register(ProviderEnum.OPENAI, openai)
    app.state.registry.register(ProviderEnum.ANTHROPIC, anthropic)

    response = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1][0] == "done"
    assert events[-1][1]["provider"] == "anthropic"

    assert len(openai.calls) == 1
    assert len(anthropic.calls) == 1
    record = await db_session.scalar(select(Usage_Record))
    assert record is not None
    assert record.provider == "anthropic"


async def test_streaming_ambiguous_pre_stream_failure_is_a_clean_json_error(
    client, stub_openai, usage_count
):
    """An ambiguous failure with no candidate having produced anything yet
    behaves like the non-streaming path: a normal JSON error, not SSE — HTTP
    status hasn't been committed, so this must not silently become a stream.
    """
    openai = FailingAdapter(UpstreamAmbiguous("openai timed out"))
    app.state.registry.register(ProviderEnum.OPENAI, openai)

    response = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": True,
        },
    )

    assert response.status_code == 502
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert response.json()["error_code"] == "UPSTREAM_AMBIGUOUS"
    assert await usage_count() == 0


async def test_streaming_mid_stream_interruption_bills_partial_and_cannot_fail_over(
    client, stub_openai, db_session
):
    """Once content has reached the caller, a break can only be reported
    in-band (`event: error`) and bills whatever partial usage is known — it
    can never fail over to the next candidate, even though one is configured.
    """
    partial = ChatResponse(
        provider=ProviderEnum.OPENAI,
        model="gpt-4o",
        content="par",
        tokens_in=5,
        tokens_out=3,
        finish_reason="error",
        request_id="stub-interrupted",
    )
    openai = InterruptingAdapter(
        ProviderEnum.OPENAI,
        error=UpstreamStreamInterrupted("connection dropped mid-stream", partial=partial),
        deltas=["par"],
    )
    anthropic = StubAdapter(ProviderEnum.ANTHROPIC)
    app.state.registry.register(ProviderEnum.OPENAI, openai)
    app.state.registry.register(ProviderEnum.ANTHROPIC, anthropic)

    response = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={
            "model": "default",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": True,
        },
    )

    assert response.status_code == 200  # already committed by the time it broke
    events = _parse_sse(response.text)
    assert events[0] == ("delta", {"content": "par"})
    assert events[-1][0] == "error"
    assert events[-1][1]["error_code"] == "UPSTREAM_AMBIGUOUS"

    # never failed over: content had already left us for this candidate.
    assert len(openai.calls) == 1
    assert len(anthropic.calls) == 0

    record = await db_session.scalar(select(Usage_Record))
    assert record is not None
    assert record.provider == "openai"
    assert record.token_in == 5
    assert record.token_out == 3
    assert record.finish_reason == "error"


async def test_streaming_idempotent_replay(client, stub_openai, usage_count):
    """Two streamed requests under the same Idempotency-Key hit the provider
    once; the replay is served from the stored response, not re-executed.
    """
    payload = {
        "model": "default",
        "messages": [{"role": "user", "content": "ping"}],
        "stream": True,
    }
    headers = {**AUTH_HEADERS, "Idempotency-Key": "stream-abc-123"}

    first = await client.post("/v1/chat/completions", headers=headers, json=payload)
    second = await client.post("/v1/chat/completions", headers=headers, json=payload)

    assert first.status_code == second.status_code == 200
    first_done = _parse_sse(first.text)[-1][1]
    second_done = _parse_sse(second.text)[-1][1]
    assert first_done == second_done

    assert len(stub_openai.calls) == 1
    assert await usage_count() == 1
