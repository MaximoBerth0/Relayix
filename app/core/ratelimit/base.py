"""The rate-limiter contract.

Everything in the request path depends on this Protocol, never on a concrete
backend. That is what lets the Redis limiter (infra) and the in-memory limiter
(core) be swapped or composed without any caller knowing the difference.
"""

from typing import Protocol
from uuid import UUID


class RateLimiter(Protocol):
    """One method: decide whether this key may make one more request.

    `check` is pass-or-raise. It returns None when the request is allowed and
    raises `RateLimitExceeded` when it is not. `rpm` is the caller's own limit
    (requests per minute), resolved from the api key at the call site.
    """

    async def check(self, key: UUID, rpm: int) -> None: ...
