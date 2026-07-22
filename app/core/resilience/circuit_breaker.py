import asyncio
import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """per-provider failure gate. Provider-agnostic: it only counts outcomes and
    decides whether the next call is allowed

    CLOSED    -> calls pass, trips OPEN after `fail_threshold` consecutive failures
    OPEN      -> calls rejected until `reset_timeout_s` elapses, then HALF_OPEN
    HALF_OPEN -> a single trial call is allowed; success closes, failure re-opens
    """

    def __init__(self, fail_threshold: int, reset_timeout_s: float) -> None:
        self._fail_threshold = fail_threshold
        self._reset_timeout_s = reset_timeout_s
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def allow(self) -> bool:
        async with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self._reset_timeout_s:
                    self._state = CircuitState.HALF_OPEN
                    self._trial_in_flight = True
                    return True
                return False

            # HALF_OPEN: admit exactly one trial while it is outstanding.
            if not self._trial_in_flight:
                self._trial_in_flight = True
                return True
            return False

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._trial_in_flight = False
            self._state = CircuitState.CLOSED

    async def record_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            self._trial_in_flight = False
            if (
                self._state == CircuitState.HALF_OPEN
                or self._failures >= self._fail_threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    async def record_abort(self) -> None:
        """release an in-flight trial without recording success or failure
        """
        async with self._lock:
            self._trial_in_flight = False
