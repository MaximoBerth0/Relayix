"""InMemoryRateLimiter: window math and reset timing for the token-bucket
algorithm, exercised directly (no Redis, no HTTP).

Time is driven through a fake clock so refill math is deterministic and
instant, the same pattern used for the circuit breaker's tests.
"""

import asyncio
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import RateLimitExceeded
from app.core.ratelimit import token_bucket
from app.core.ratelimit.token_bucket import InMemoryRateLimiter

KEY: UUID = uuid4()


class FakeClock:
    def __init__(self) -> None:
        self._now = 1_000.0

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(token_bucket, "time", fake)
    return fake


async def test_a_new_key_starts_with_a_full_bucket(clock):
    """First call for a brand-new caller must succeed, not start at zero."""
    limiter = InMemoryRateLimiter()

    await limiter.check(KEY, rpm=60)  # raises on failure


async def test_capacity_calls_succeed_before_the_bucket_is_exhausted(clock):
    limiter = InMemoryRateLimiter()

    for _ in range(5):
        await limiter.check(KEY, rpm=5)


async def test_the_call_past_capacity_is_rejected(clock):
    limiter = InMemoryRateLimiter()

    for _ in range(5):
        await limiter.check(KEY, rpm=5)

    with pytest.raises(RateLimitExceeded):
        await limiter.check(KEY, rpm=5)


async def test_retry_after_reflects_the_time_to_the_next_token(clock):
    """rpm=1 gives a bucket of capacity 1 that refills one token every 60s, so
    right after exhausting it the caller must wait ~60s for the next one.
    """
    limiter = InMemoryRateLimiter()
    await limiter.check(KEY, rpm=1)  # spends the only token

    with pytest.raises(RateLimitExceeded) as excinfo:
        await limiter.check(KEY, rpm=1)

    assert excinfo.value.retry_after_s == pytest.approx(60.0, abs=0.01)


async def test_elapsed_time_refills_the_bucket(clock):
    limiter = InMemoryRateLimiter()
    await limiter.check(KEY, rpm=60)  # 59 tokens left

    clock.advance(30)  # +30 tokens at 1/s

    for _ in range(31):
        await limiter.check(KEY, rpm=60)


async def test_idle_time_cannot_bank_more_than_capacity(clock):
    """Refill caps at capacity, an idle caller doesn't accrue unlimited burst."""
    limiter = InMemoryRateLimiter()
    await limiter.check(KEY, rpm=5)  # 4 tokens left

    clock.advance(3600)  # would be +300 tokens uncapped

    for _ in range(5):
        await limiter.check(KEY, rpm=5)
    with pytest.raises(RateLimitExceeded):
        await limiter.check(KEY, rpm=5)


async def test_different_keys_have_independent_buckets(clock):
    limiter = InMemoryRateLimiter()
    other_key = uuid4()

    for _ in range(5):
        await limiter.check(KEY, rpm=5)

    await limiter.check(other_key, rpm=5)  # a fresh bucket, unaffected by KEY


async def test_concurrent_checks_never_oversell_the_bucket(clock):
    """The lock must serialize refill+spend: N concurrent callers against a
    bucket of capacity N must admit exactly N, not more.
    """
    limiter = InMemoryRateLimiter()
    capacity = 20

    results = await asyncio.gather(
        *(limiter.check(KEY, rpm=capacity) for _ in range(capacity + 10)),
        return_exceptions=True,
    )

    admitted = [r for r in results if r is None]
    rejected = [r for r in results if isinstance(r, RateLimitExceeded)]
    assert len(admitted) == capacity
    assert len(rejected) == 10
