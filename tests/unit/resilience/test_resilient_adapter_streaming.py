"""ResilientAdapter.stream(): the circuit breaker + timeout wrapping around a
streaming provider call, exercised directly (no HTTP, no DB, no Redis).

The key property under test: once an inner stream has yielded at least one
delta, a later failure must be reported as UpstreamStreamInterrupted (can't
fail over — content is already out) rather than UpstreamAmbiguous, and the
breaker must be credited with exactly one outcome per call, never zero or two.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.core.exceptions import UpstreamAmbiguous, UpstreamStreamInterrupted
from app.core.resilience.circuit_breaker import CircuitBreaker, CircuitState
from app.core.resilience.resilient_adapter import ResilientAdapter
from app.models.domain.chat import ChatRequest, ChatResponse, ChatStreamDelta, Message
from app.models.domain.enums import ProviderEnum

REQUEST = ChatRequest(model="gpt-4o", messages=[Message(role="user", content="hi")])


class ScriptedStreamAdapter:
    """A ProviderAdapter double whose stream() is scripted per-test: yield some
    deltas, optionally sleep, optionally raise. Records whether it was closed
    without being fully drained (i.e. via `aclose()`, as a real cancellation
    would trigger).
    """

    def __init__(self, deltas: list[str], *, delay_s: float = 0.0, error: Exception | None = None):
        self._deltas = deltas
        self._delay_s = delay_s
        self._error = error
        self.closed_early = False

    async def complete(self, request: ChatRequest) -> ChatResponse:  # pragma: no cover - unused here
        raise NotImplementedError

    async def stream(self, request: ChatRequest) -> AsyncIterator:
        try:
            for text in self._deltas:
                yield ChatStreamDelta(content=text)
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            if self._error is not None:
                raise self._error
            yield ChatResponse(
                provider=ProviderEnum.OPENAI,
                model=request.model,
                content="".join(self._deltas),
                tokens_in=1,
                tokens_out=len(self._deltas),
                finish_reason="stop",
                request_id="scripted",
            )
        except GeneratorExit:
            self.closed_early = True
            raise


def make_resilient(inner, *, timeout_s: float = 0.05) -> ResilientAdapter:
    breaker = CircuitBreaker(fail_threshold=2, reset_timeout_s=30)
    return ResilientAdapter(
        inner=inner, provider=ProviderEnum.OPENAI, breaker=breaker, timeout_s=timeout_s
    ), breaker


async def drain(adapter: ResilientAdapter) -> list:
    return [event async for event in adapter.stream(REQUEST)]


async def test_successful_stream_forwards_every_event_and_records_success():
    inner = ScriptedStreamAdapter(["hel", "lo"])
    adapter, breaker = make_resilient(inner)

    events = await drain(adapter)

    assert [e.content for e in events[:-1]] == ["hel", "lo"]
    assert isinstance(events[-1], ChatResponse)
    assert breaker.state is CircuitState.CLOSED
    assert await breaker.allow() == (True, 0)  # generation never advanced


async def test_timeout_before_any_delta_is_ambiguous():
    """A stall on the very first read is a pre-commit failure: safe to fail
    over, exactly like a non-streaming timeout.
    """
    inner = ScriptedStreamAdapter([], delay_s=1.0)
    adapter, breaker = make_resilient(inner, timeout_s=0.02)

    with pytest.raises(UpstreamAmbiguous):
        await drain(adapter)

    # exactly one failure recorded — not zero, not double-counted.
    assert breaker.state is CircuitState.CLOSED  # threshold is 2
    with pytest.raises(UpstreamAmbiguous):
        await drain(adapter)
    assert breaker.state is CircuitState.OPEN


async def test_timeout_after_a_delta_is_interrupted_not_ambiguous():
    """Once the caller already has partial content, the failure can no longer
    be classified as a clean pre-stream ambiguity.
    """
    inner = ScriptedStreamAdapter(["par"], delay_s=1.0)
    adapter, breaker = make_resilient(inner, timeout_s=0.02)

    events = []
    with pytest.raises(UpstreamStreamInterrupted):
        async for event in adapter.stream(REQUEST):
            events.append(event)

    assert [e.content for e in events] == ["par"]
    # still exactly one failure recorded for this call.
    assert breaker.state is CircuitState.CLOSED
    with pytest.raises(UpstreamStreamInterrupted):
        await drain(adapter)
    assert breaker.state is CircuitState.OPEN


async def test_provider_error_after_a_delta_is_reclassified_as_interrupted():
    """The inner adapter itself raising mid-stream (not just a timeout) must
    also be re-surfaced as non-retryable once content has already gone out 
    this is the inner adapter's own job (see AnthropicAdapter/OpenAIAdapter's
    `_raise_stream_failure`), so this pins that ResilientAdapter passes such
    an UpstreamStreamInterrupted through unchanged rather than re-wrapping it.
    """
    boom = UpstreamStreamInterrupted("provider broke mid-stream")
    inner = ScriptedStreamAdapter(["par"], error=boom)
    adapter, breaker = make_resilient(inner)

    with pytest.raises(UpstreamStreamInterrupted) as excinfo:
        await drain(adapter)

    assert excinfo.value is boom
    assert breaker.state is CircuitState.CLOSED  # one failure, threshold is 2


async def test_client_disconnect_aborts_without_deciding_and_closes_the_inner_stream():
    """A caller that stops iterating early (client disconnect) must not count
    as a provider failure or success, but must still release the upstream
    stream so its connection isn't leaked.
    """
    inner = ScriptedStreamAdapter(["hel", "lo"])
    adapter, breaker = make_resilient(inner)

    agen = adapter.stream(REQUEST)
    first = await anext(agen)
    assert first.content == "hel"

    await agen.aclose()

    assert inner.closed_early is True
    assert breaker.state is CircuitState.CLOSED
    # the trial slot wasn't left dangling: a fresh CLOSED breaker still just allows.
    assert (await breaker.allow())[0] is True
