"""The rate-limiter contract.

Everything in the request path depends on this Protocol, never on a concrete
backend. That is what lets the Redis limiter (infra) and the in-memory limiter
(core) be swapped or composed without any caller knowing the difference.
"""

from typing import Protocol
from uuid import UUID


class RateLimiter(Protocol):
    """A token bucket per key that refills `per_minute` units every minute.

    `check` is pass-or-raise: it spends `cost` units and returns None, or raises
    `RateLimitExceeded` when the bucket can't cover it. A cost larger than the
    whole bucket passes only when the bucket is full, so it can never deadlock.

    `charge` always applies: a positive cost spends (the bucket may go negative,
    which delays the next requests), a negative one refunds. It is how a cost
    estimated at admission is corrected once the real one is known.
    """

    async def check(self, key: UUID, per_minute: int, cost: float = 1.0) -> None: ...

    async def charge(self, key: UUID, per_minute: int, cost: float) -> None: ...
