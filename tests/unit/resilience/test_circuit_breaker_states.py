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


async def allows(breaker: CircuitBreaker) -> bool:
    """The verdict alone, for assertions that do not care about the generation."""
    verdict, _ = await breaker.allow()
    return verdict


async def trip(breaker: CircuitBreaker, failures: int) -> None:
    """Record `failures` failures through the breaker's own API.

    The generation is read once: while CLOSED it cannot change until the
    threshold trips, and a burst that overshoots it *should* have its extra
    failures discarded as stale.
    """
    _, generation = await breaker.allow()
    for _ in range(failures):
        await breaker.record_failure(generation)


async def succeed(breaker: CircuitBreaker) -> None:
    """Record one success under the current generation."""
    _, generation = await breaker.allow()
    await breaker.record_success(generation)


# CLOSED


async def test_starts_closed_and_allows(clock):
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    assert breaker.state is CLOSED
    assert await allows(breaker) is True


async def test_failures_below_threshold_stay_closed(clock):
    """The gate only trips on the threshold itself, not on the way up to it."""
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    await trip(breaker, 2)

    assert breaker.state is CLOSED
    assert await allows(breaker) is True


async def test_reaching_the_threshold_opens(clock):
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    await trip(breaker, 3)

    assert breaker.state is OPEN


async def test_threshold_of_one_opens_on_the_first_failure(clock):
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)

    await trip(breaker, 1)

    assert breaker.state is OPEN


async def test_success_resets_the_failure_count(clock):
    """Failures must be *consecutive*: an intervening success clears the tally,
    so a provider that fails intermittently never trips the gate.
    """
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    await trip(breaker, 2)
    await succeed(breaker)
    await trip(breaker, 2)

    assert breaker.state is CLOSED


async def test_closed_allows_are_unlimited(clock):
    """No trial-call accounting while closed: the gate is fully open."""
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    verdicts = await asyncio.gather(*(breaker.allow() for _ in range(20)))

    assert all(verdict for verdict, _ in verdicts)


async def test_closed_hands_out_one_generation_to_everyone(clock):
    """Concurrent calls in a stable CLOSED circuit share a generation, so their
    failures accumulate against the same threshold.
    """
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)

    verdicts = await asyncio.gather(*(breaker.allow() for _ in range(20)))

    assert len({generation for _, generation in verdicts}) == 1


# OPEN


async def test_open_rejects_before_the_timeout_elapses(clock):
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await trip(breaker, 1)

    clock.advance(29.9)

    assert await allows(breaker) is False
    assert breaker.state is OPEN


async def test_open_stays_open_without_an_allow_call(clock):
    """The transition is lazy: it happens inside `allow()`, so an expired
    breaker that nobody has asked about still reports OPEN.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await trip(breaker, 1)

    clock.advance(60)

    assert breaker.state is OPEN


async def test_elapsed_timeout_moves_to_half_open_on_the_next_allow(clock):
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await trip(breaker, 1)

    clock.advance(30.1)

    assert await allows(breaker) is True
    assert breaker.state is HALF_OPEN


async def test_the_timeout_boundary_is_inclusive(clock):
    """`>= reset_timeout_s`: landing exactly on the deadline admits the trial."""
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await trip(breaker, 1)

    clock.advance(30.0)

    assert await allows(breaker) is True


async def test_zero_timeout_is_half_open_immediately(clock):
    """Degenerate config: the breaker still counts failures, but never parks in
    OPEN. Worth pinning so a misconfigured 0 fails loudly in review, not prod.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=0)
    await trip(breaker, 1)

    assert await allows(breaker) is True
    assert breaker.state is HALF_OPEN


# HALF_OPEN


async def test_half_open_admits_exactly_one_trial(clock):
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await trip(breaker, 1)
    clock.advance(31)

    assert await allows(breaker) is True
    assert await allows(breaker) is False
    assert await allows(breaker) is False


async def test_concurrent_probes_yield_a_single_trial(clock):
    """The real reason `_lock` exists: when the timeout expires under load,
    every in-flight caller races into `allow()` at once and exactly one may pass.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await trip(breaker, 1)
    clock.advance(31)

    verdicts = await asyncio.gather(*(breaker.allow() for _ in range(50)))

    assert sum(verdict for verdict, _ in verdicts) == 1


async def test_trial_success_closes_the_circuit(clock):
    breaker = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)
    await trip(breaker, 3)
    clock.advance(31)
    _, generation = await breaker.allow()

    await breaker.record_success(generation)

    assert breaker.state is CLOSED
    assert await allows(breaker) is True


async def test_trial_failure_reopens_regardless_of_the_threshold(clock):
    """A half-open failure is not counted against `fail_threshold`; one is
    enough. The provider already proved it was down.
    """
    breaker = CircuitBreaker(fail_threshold=10, reset_timeout_s=30)
    await trip(breaker, 10)
    clock.advance(31)
    _, generation = await breaker.allow()

    await breaker.record_failure(generation)

    assert breaker.state is OPEN
    assert await allows(breaker) is False


async def test_trial_failure_restarts_the_timeout(clock):
    """Reopening resets the clock, so a flapping provider gets a fresh full
    cooldown instead of being retried every tick.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await trip(breaker, 1)
    clock.advance(31)
    _, generation = await breaker.allow()
    await breaker.record_failure(generation)

    clock.advance(29)
    assert await allows(breaker) is False

    clock.advance(2)
    assert await allows(breaker) is True


async def test_abort_releases_the_trial_without_deciding(clock):
    """A trial cancelled before it reached the provider (client disconnect,
    timeout on our side) must not count as evidence either way, but it must
    free the slot so the next caller can probe.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    await trip(breaker, 1)
    clock.advance(31)
    _, generation = await breaker.allow()

    await breaker.record_abort(generation)

    assert breaker.state is HALF_OPEN
    assert await allows(breaker) is True


# Generations: outcomes from a call admitted under an older state must not vote


async def test_success_from_before_the_trip_does_not_reclose(clock):
    """The race this guards: a burst of calls is admitted while CLOSED, enough
    of them fail to open the gate, and then a straggler from that same burst
    finally succeeds. It must not undo the trip.
    """
    breaker = CircuitBreaker(fail_threshold=2, reset_timeout_s=30)
    _, stale = await breaker.allow()

    await breaker.record_failure(stale)
    await breaker.record_failure(stale)
    assert breaker.state is OPEN

    await breaker.record_success(stale)

    assert breaker.state is OPEN
    assert await allows(breaker) is False


async def test_failure_from_before_the_trip_does_not_extend_the_cooldown(clock):
    """Stragglers from the burst that opened the gate must not keep pushing the
    reset deadline out, which would leave the provider quarantined forever.
    """
    breaker = CircuitBreaker(fail_threshold=2, reset_timeout_s=30)
    _, stale = await breaker.allow()
    await breaker.record_failure(stale)
    await breaker.record_failure(stale)

    clock.advance(20)
    await breaker.record_failure(stale)  # late arrival, 20s into the cooldown

    clock.advance(11)
    assert await allows(breaker) is True, "the cooldown must run from the trip"


async def test_stale_failure_does_not_reopen_a_recovered_circuit(clock):
    """A trial closed the circuit; a failure left over from the outage arrives
    afterwards and must not drag it straight back to OPEN.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    _, stale = await breaker.allow()
    await breaker.record_failure(stale)
    clock.advance(31)
    _, trial = await breaker.allow()
    await breaker.record_success(trial)

    await breaker.record_failure(stale)

    assert breaker.state is CLOSED


async def test_stale_abort_does_not_free_the_current_trial(clock):
    """Otherwise a late abort from the previous window would hand out a second
    concurrent trial, and the half-open gate would leak calls.
    """
    breaker = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    _, stale = await breaker.allow()
    await breaker.record_failure(stale)
    clock.advance(31)
    await breaker.allow()  # the one trial for this window

    await breaker.record_abort(stale)

    assert await allows(breaker) is False


async def test_stale_success_does_not_clear_the_failure_count(clock):
    """A slow success from before the last trip must not wipe the tally that a
    fresh outage is building up.
    """
    breaker = CircuitBreaker(fail_threshold=2, reset_timeout_s=30)
    _, stale = await breaker.allow()
    await breaker.record_failure(stale)
    await breaker.record_failure(stale)
    clock.advance(31)
    _, trial = await breaker.allow()
    await breaker.record_success(trial)

    _, current = await breaker.allow()
    await breaker.record_failure(current)
    await breaker.record_success(stale)
    await breaker.record_failure(current)

    assert breaker.state is OPEN


# Full cycles


async def test_closed_open_half_open_closed(clock):
    breaker = CircuitBreaker(fail_threshold=2, reset_timeout_s=30)

    assert breaker.state is CLOSED
    await trip(breaker, 2)
    assert breaker.state is OPEN

    clock.advance(31)
    verdict, generation = await breaker.allow()
    assert verdict is True
    assert breaker.state is HALF_OPEN

    await breaker.record_success(generation)
    assert breaker.state is CLOSED


async def test_recovery_leaves_a_clean_slate(clock):
    """After a full recovery the breaker must need the *whole* threshold again,
    not a single failure left over from the previous outage.
    """
    breaker = CircuitBreaker(fail_threshold=2, reset_timeout_s=30)
    await trip(breaker, 2)
    clock.advance(31)
    _, generation = await breaker.allow()
    await breaker.record_success(generation)

    await trip(breaker, 1)
    assert breaker.state is CLOSED

    await trip(breaker, 1)
    assert breaker.state is OPEN


async def test_repeated_failed_recoveries_stay_open(clock):
    """Provider stays down across several cooldowns: each probe reopens, and the
    breaker never leaks more than one call per window.
    """
    breaker = CircuitBreaker(fail_threshold=2, reset_timeout_s=30)
    await trip(breaker, 2)

    for _ in range(5):
        clock.advance(31)
        verdict, generation = await breaker.allow()
        assert verdict is True
        assert await allows(breaker) is False
        await breaker.record_failure(generation)
        assert breaker.state is OPEN
