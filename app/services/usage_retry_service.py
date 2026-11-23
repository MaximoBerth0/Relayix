"""Drains the pending usage queue: records whose insert failed in the request path."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Protocol

from app.models.domain.pending_usage import PendingUsage
from app.models.domain.usage_record import UsageRecord

logger = logging.getLogger(__name__)

Persist = Callable[[UsageRecord], Awaitable[None]]


class UsageQueue(Protocol):
    async def claim(self, timeout_s: float) -> PendingUsage | None: ...
    async def ack(self, entry: PendingUsage) -> None: ...
    async def retry(self, entry: PendingUsage) -> None: ...
    async def bury(self, entry: PendingUsage) -> None: ...


class RetryOutcome(Enum):
    EMPTY = "empty"
    SAVED = "saved"
    RETRY = "retry"
    DEAD = "dead"


class UsageRetryService:
    def __init__(self, queue: UsageQueue, persist: Persist, max_attempts: int) -> None:
        self._queue = queue
        self._persist = persist
        self._max_attempts = max_attempts

    async def process_next(self, timeout_s: float) -> RetryOutcome:
        entry = await self._queue.claim(timeout_s)
        if entry is None:
            return RetryOutcome.EMPTY

        extra = {"usage_id": str(entry.record.id), "attempts": entry.attempts + 1}
        try:
            await self._persist(entry.record)
        except Exception:
            if entry.attempts + 1 >= self._max_attempts:
                logger.exception("usage record dead-lettered", extra=extra)
                await self._queue.bury(entry)
                return RetryOutcome.DEAD
            logger.warning("usage record retry failed", extra=extra, exc_info=True)
            await self._queue.retry(entry)
            return RetryOutcome.RETRY

        await self._queue.ack(entry)
        logger.info("usage record recovered", extra=extra)
        return RetryOutcome.SAVED
