"""Requests that hit a bug nobody anticipated still get the same JSON error
shape as everything else, instead of a bare 500 or a silently cut stream.

The gateway service itself never raises anything but AppError subclasses, so
this exercises the catch-all handlers directly with a double that raises a
plain RuntimeError, the one failure mode a genuine bug could still produce.
"""

import json

from fixtures.factories import AUTH_HEADERS

from app.api.deps import get_gateway_service
from app.main import app


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


class _BrokenGateway:
    """A GatewayService double that raises something other than AppError."""

    async def complete(self, request, api_key_id):
        raise RuntimeError("boom")

    async def stream(self, request, api_key_id):
        async def gen():
            raise RuntimeError("boom")
            yield  # pragma: no cover - makes this an async generator function

        return gen()


async def test_an_unexpected_exception_returns_the_generic_json_error_shape(
    client, seed_api_key
):
    app.dependency_overrides[get_gateway_service] = lambda: _BrokenGateway()

    response = await client.post(
        "/v1/chat/completions",
        headers=AUTH_HEADERS,
        json={"model": "default", "messages": [{"role": "user", "content": "ping"}]},
    )

    assert response.status_code == 500
    assert response.json() == {
        "error_code": "INTERNAL_ERROR",
        "message": "Internal server error",
    }


async def test_an_unexpected_exception_mid_stream_emits_a_generic_error_event(
    client, seed_api_key
):
    """The HTTP status is already committed to 200 by the time a streamed
    request breaks, so the only way left to signal failure is in-band.
    """
    app.dependency_overrides[get_gateway_service] = lambda: _BrokenGateway()

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
    assert events[-1] == (
        "error",
        {"error_code": "INTERNAL_ERROR", "message": "Internal server error"},
    )
