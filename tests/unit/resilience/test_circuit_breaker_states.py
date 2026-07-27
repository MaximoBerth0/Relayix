"""Circuit breaker state machine: when the gate opens, when it closes, and how
many calls it lets through in between.

The OPEN -> HALF_OPEN edge is time-based, so these tests drive a fake clock
instead of sleeping: `clock.advance(...)` is deterministic and instant, where a
real `asyncio.sleep` would be both slow and flaky under load
"""

import asyncio

import pytest

from app.core.resilience import circuit_breaker
from app.core.resilience.circuit_breaker import CircuitBreaker, CircuitState

CLOSED = CircuitState.CLOSED
OPEN = CircuitState.OPEN
HALF_OPEN = CircuitState.HALF_OPEN


class FakeClock:
    """Stands in for the `time` module inside circuit_breaker. Only `monotonic`
    is used there, so that is all it needs to provide.
    """
 
    def __init__(self) -> None:
        self._now = 1_000.0

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    # this replaces "time" module with the fake clock
    monkeypatch.setattr(circuit_breaker, "time", fake)
    return fake


async def trip(breaker: CircuitBreaker, failures: int) -> None:
    """Record `failures` failures through the breaker's own API."""
    for _ in range(failures):
        await breaker.record_failure()


# CLOSED


async def test_starts_closed_and_allows(clock):
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    assert breaker.state is CLOSED
    assert await breaker.allow() is True


async def test_failures_below_threshold_stay_closed(clock):
    """The gate only trips on the threshold itself, not on the way up to it."""
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    await trip(breaker, 2)

    assert breaker.state is CLOSED
    assert await breaker.allow() is True


async def test_reaching_the_threshold_opens(clock):
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    await trip(breaker, 3)

    assert breaker.state is OPEN


async def test_threshold_of_one_opens_on_the_first_failure(clock):
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)

    await breaker.record_failure()

    assert breaker.state is OPEN


async def test_success_resets_the_failure_count(clock):
    """Failures must be *consecutive*: an intervening success clears the tally,
    so a provider that fails intermittently never trips the gate.
    """
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    await trip(breaker, 2)
    await breaker.record_success()
    await trip(breaker, 2)

    assert breaker.state is CLOSED


async def test_closed_allows_are_unlimited(clock):
    """No trial-call accounting while closed: the gate is fully open."""
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    assert all(await asyncio.gather(*(breaker.allow() for _ in range(20))))


# OPEN


async def test_open_rejects_before_the_timeout_elapses(clock):
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await breaker.record_failure()

    clock.advance(29.9)

    assert await breaker.allow() is False
    assert breaker.state is OPEN


async def test_open_stays_open_without_an_allow_call(clock):
    """The transition is lazy: it happens inside `allow()`, so an expired
    breaker that nobody has asked about still reports OPEN.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await breaker.record_failure()

    clock.advance(60)

    assert breaker.state is OPEN


async def test_elapsed_timeout_moves_to_half_open_on_the_next_allow(clock):
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await breaker.record_failure()

    clock.advance(30.1)

    assert await breaker.allow() is True
    assert breaker.state is HALF_OPEN


async def test_the_timeout_boundary_is_inclusive(clock):
    """`>= reset_timeout_s`: landing exactly on the deadline admits the trial."""
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await breaker.record_failure()

    clock.advance(30.0)

    assert await breaker.allow() is True


async def test_zero_timeout_is_half_open_immediately(clock):
    """Degenerate config: the breaker still counts failures, but never parks in
    OPEN. Worth pinning so a misconfigured 0 fails loudly in review, not prod.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=0)
    await breaker.record_failure()

    assert await breaker.allow() is True
    assert breaker.state is HALF_OPEN


# HALF_OPEN


async def test_half_open_admits_exactly_one_trial(clock):
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await breaker.record_failure()
    clock.advance(31)

    assert await breaker.allow() is True
    assert await breaker.allow() is False
    assert await breaker.allow() is False


async def test_concurrent_probes_yield_a_single_trial(clock):
    """The real reason `_lock` exists: when the timeout expires under load,
    every in-flight caller races into `allow()` at once and exactly one may pass.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await breaker.record_failure()
    clock.advance(31)

    verdicts = await asyncio.gather(*(breaker.allow() for _ in range(50)))

    assert sum(verdicts) == 1


async def test_trial_success_closes_the_circuit(clock):
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)
    await trip(breaker, 3)
    clock.advance(31)
    await breaker.allow()

    await breaker.record_success()

    assert breaker.state is CLOSED
    assert await breaker.allow() is True


async def test_trial_failure_reopens_regardless_of_the_threshold(clock):
    """A half-open failure is not counted against `fail_threshold`; one is
    enough. The provider already proved it was down.
    """
    breaker = CircuitBreaker(fail_threshold=10, reset_timeout_s=30)
    await trip(breaker, 10)
    clock.advance(31)
    await breaker.allow()

    await breaker.record_failure()

    assert breaker.state is OPEN
    assert await breaker.allow() is False


async def test_trial_failure_restarts_the_timeout(clock):
    """Reopening resets the clock, so a flapping provider gets a fresh full
    cooldown instead of being retried every tick.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await breaker.record_failure()
    clock.advance(31)
    await breaker.allow()
    await breaker.record_failure()

    clock.advance(29)
    assert await breaker.allow() is False

    clock.advance(2)
    assert await breaker.allow() is True


async def test_abort_releases_the_trial_without_deciding(clock):
    """A trial cancelled before it reached the provider (client disconnect,
    timeout on our side) must not count as evidence either way, but it must
    free the slot so the next caller can probe.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await breaker.record_failure()
    clock.advance(31)
    await breaker.allow()

    await breaker.record_abort()

    assert breaker.state is HALF_OPEN
    assert await breaker.allow() is True


async def test_abort_while_closed_is_a_no_op(clock):
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)
    await trip(breaker, 2)

    await breaker.record_abort()

    assert breaker.state is CLOSED
    await breaker.record_failure()
    assert breaker.state is OPEN, "abort must not have cleared the failure count"


# Full cycles


async def test_closed_open_half_open_closed(clock):
    breaker = CircuitBreaker(fail_threshold=2, reset_timeout_s=30)

    assert breaker.state is CLOSED
    await trip(breaker, 2)
    assert breaker.state is OPEN

    clock.advance(31)
    assert await breaker.allow() is True
    assert breaker.state is HALF_OPEN

    await breaker.record_success()
    assert breaker.state is CLOSED


async def test_recovery_leaves_a_clean_slate(clock):
    """After a full recovery the breaker must need the *whole* threshold again,
    not a single failure left over from the previous outage.
    """
    breaker = CircuitBreaker(fail_threshold=2, reset_timeout_s=30)
    await trip(breaker, 2)
    clock.advance(31)
    await breaker.allow()
    await breaker.record_success()

    await breaker.record_failure()
    assert breaker.state is CLOSED

    await breaker.record_failure()
    assert breaker.state is OPEN


async def test_repeated_failed_recoveries_stay_open(clock):
    """Provider stays down across several cooldowns: each probe reopens, and the
    breaker never leaks more than one call per window.
    """
    breaker = CircuitBreaker(fail_threshold=2, reset_timeout_s=30)
    await trip(breaker, 2)

    for _ in range(5):
        clock.advance(31)
        assert await breaker.allow() is True
        assert await breaker.allow() is False
        await breaker.record_failure()
        assert breaker.state is OPEN
