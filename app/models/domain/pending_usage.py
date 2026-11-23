from dataclasses import dataclass

from app.models.domain.usage_record import UsageRecord


@dataclass(frozen=True)
class PendingUsage:
    """A usage record waiting in the retry queue, with how often it already failed."""

    record: UsageRecord
    attempts: int
    raw: bytes  # the exact queue entry, needed to remove it once handled
