"""ResilientAdapter.complete(): timeout handling and circuit-breaker bookkeeping
for the non-streaming path, exercised directly (no HTTP, no DB, no Redis).

test_resilient_adapter_streaming.py covers .stream(); this file covers the
twin .complete() method, which had no coverage at all. The property under
test: a bare timeout is reclassified as UpstreamAmbiguous (never safe to
retry blindly), while an error the inner adapter already classified is
forwarded unchanged, and every outcome credits the breaker exactly once.
"""

import asyncio

import pytest

from app.core.exceptions import CircuitOpen, UpstreamAmbiguous, UpstreamUnavailable
from app.core.resilience.circuit_breaker import CircuitBreaker, CircuitState
from app.core.resilience.resilient_adapter import ResilientAdapter
from app.models.domain.chat import ChatRequest, ChatResponse, Message
from app.models.domain.enums import ProviderEnum

REQUEST = ChatRequest(model="gpt-4o", messages=[Message(role="user", content="hi")])

RESPONSE = ChatResponse(
    provider=ProviderEnum.OPENAI,
    model="gpt-4o",
    content="hi back",
    tokens_in=1,
    tokens_out=1,
    finish_reason="stop",
    request_id="scripted",
)


class ScriptedAdapter:
    """A ProviderAdapter double whose complete() is scripted per-test: sleep,
    then either raise a given exception or return the canned response.
    """

    def __init__(self, *, delay_s: float = 0.0, error: BaseException | None = None):
        self._delay_s = delay_s
        self._error = error
        self.calls: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        return RESPONSE

    def stream(self, request):  # pragma: no cover - unused here
        raise NotImplementedError


def make_resilient(
    inner, *, timeout_s: float = 0.05, fail_threshold: int = 2
) -> tuple[ResilientAdapter, CircuitBreaker]:
    breaker = CircuitBreaker(fail_threshold=fail_threshold, reset_timeout_s=30)
    adapter = ResilientAdapter(
        inner=inner, provider=ProviderEnum.OPENAI, breaker=breaker, timeout_s=timeout_s
    )
    return adapter, breaker


async def test_success_returns_the_response_and_records_success():
    adapter, breaker = make_resilient(ScriptedAdapter())

    response = await adapter.complete(REQUEST)

    assert response is RESPONSE
    assert breaker.state is CircuitState.CLOSED
    assert await breaker.allow() == (True, 0)  # generation never advanced


async def test_timeout_is_reclassified_as_ambiguous_and_counts_as_a_failure():
    """A stall is never safe to retry blindly: the request may already have
    reached the model, so it surfaces as UpstreamAmbiguous rather than the
    bare TimeoutError.
    """
    adapter, breaker = make_resilient(ScriptedAdapter(delay_s=1.0), timeout_s=0.02)

    with pytest.raises(UpstreamAmbiguous):
        await adapter.complete(REQUEST)

    assert breaker.state is CircuitState.CLOSED  # threshold is 2
    with pytest.raises(UpstreamAmbiguous):
        await adapter.complete(REQUEST)
    assert breaker.state is CircuitState.OPEN


async def test_a_retryable_inner_error_passes_through_unchanged():
    """UpstreamUnavailable (provably never executed) is exactly the error
    failover retries on, ResilientAdapter must not reclassify it, only
    record it against the breaker.
    """
    boom = UpstreamUnavailable("connection refused")
    adapter, breaker = make_resilient(ScriptedAdapter(error=boom))

    with pytest.raises(UpstreamUnavailable) as excinfo:
        await adapter.complete(REQUEST)

    assert excinfo.value is boom
    assert breaker.state is CircuitState.CLOSED  # one failure, threshold is 2


async def test_a_non_retryable_inner_error_also_passes_through_unchanged():
    """UpstreamAmbiguous raised by the inner adapter itself (not a timeout)
    is likewise forwarded as-is.
    """
    boom = UpstreamAmbiguous("malformed response")
    adapter, breaker = make_resilient(ScriptedAdapter(error=boom))

    with pytest.raises(UpstreamAmbiguous) as excinfo:
        await adapter.complete(REQUEST)

    assert excinfo.value is boom
    assert breaker.state is CircuitState.CLOSED


async def test_an_unclassified_exception_still_counts_as_a_failure():
    """Anything the inner adapter raises, not just the UpstreamError family,
    must still trip the breaker, or a buggy adapter could never be
    quarantined.
    """
    adapter, breaker = make_resilient(ScriptedAdapter(error=ValueError("boom")))

    with pytest.raises(ValueError):
        await adapter.complete(REQUEST)

    assert breaker.state is CircuitState.CLOSED
    with pytest.raises(ValueError):
        await adapter.complete(REQUEST)
    assert breaker.state is CircuitState.OPEN


async def test_a_base_exception_aborts_without_deciding():
    """A BaseException that is not an Exception (cancellation) must not be
    scored as a failure or a success, only release the trial slot.
    """
    adapter, breaker = make_resilient(ScriptedAdapter(error=asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await adapter.complete(REQUEST)

    assert breaker.state is CircuitState.CLOSED
    assert (await breaker.allow())[0] is True


async def test_open_circuit_rejects_without_calling_the_inner_adapter():
    inner = ScriptedAdapter()
    adapter, breaker = make_resilient(inner, fail_threshold=1)
    _, generation = await breaker.allow()
    await breaker.record_failure(generation)
    assert breaker.state is CircuitState.OPEN

    with pytest.raises(CircuitOpen):
        await adapter.complete(REQUEST)

    assert inner.calls == []
