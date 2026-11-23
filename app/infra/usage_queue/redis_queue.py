"""Redis list holding usage records whose insert failed after the provider billed.

Reliable-queue layout: the worker moves an entry from `pending` to `processing`
with BLMOVE and only removes it once the insert committed, so a worker crash
never loses an entry. Entries that keep failing end in `dead` for a human.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from redis.asyncio import Redis

from app.models.domain.enums import ProviderEnum
from app.models.domain.pending_usage import PendingUsage
from app.models.domain.usage_record import UsageRecord

PENDING_KEY = "usage:pending"
PROCESSING_KEY = "usage:processing"
DEAD_KEY = "usage:dead"


class RedisUsageQueue:
    def __init__(self, client: Redis) -> None:
        self._client = client

    async def push(self, record: UsageRecord) -> None:
        await self._client.lpush(PENDING_KEY, _encode(record, attempts=0))

    async def claim(self, timeout_s: float) -> PendingUsage | None:
        """Move the oldest pending entry to processing, waiting up to `timeout_s`."""
        raw = await self._client.blmove(PENDING_KEY, PROCESSING_KEY, timeout_s, "RIGHT", "LEFT")
        if raw is None:
            return None
        record, attempts = _decode(raw)
        return PendingUsage(record=record, attempts=attempts, raw=raw)

    async def ack(self, entry: PendingUsage) -> None:
        await self._client.lrem(PROCESSING_KEY, 1, entry.raw)

    async def retry(self, entry: PendingUsage) -> None:
        # push before removing: a crash in between duplicates the entry, which
        # the insert dedupes, instead of losing it
        await self._client.lpush(PENDING_KEY, _encode(entry.record, entry.attempts + 1))
        await self._client.lrem(PROCESSING_KEY, 1, entry.raw)

    async def bury(self, entry: PendingUsage) -> None:
        await self._client.lpush(DEAD_KEY, _encode(entry.record, entry.attempts + 1))
        await self._client.lrem(PROCESSING_KEY, 1, entry.raw)

    async def requeue_processing(self) -> int:
        """Return entries left in processing by a crashed worker to pending."""
        moved = 0
        while await self._client.lmove(PROCESSING_KEY, PENDING_KEY, "RIGHT", "RIGHT") is not None:
            moved += 1
        return moved


def _encode(record: UsageRecord, attempts: int) -> bytes:
    return json.dumps(
        {
            "id": str(record.id),
            "api_key_id": str(record.api_key_id),
            "provider": record.provider.value,
            "model": record.model,
            "upstream_model": record.upstream_model,
            "tokens_in": record.tokens_in,
            "tokens_out": record.tokens_out,
            "tokens_estimated": record.tokens_estimated,
            "cost": str(record.cost),
            "finish_reason": record.finish_reason,
            "request_id": record.request_id,
            "created_at": record.created_at.isoformat(),
            "attempts": attempts,
        }
    ).encode()


def _decode(raw: bytes) -> tuple[UsageRecord, int]:
    body = json.loads(raw)
    record = UsageRecord(
        id=UUID(body["id"]),
        api_key_id=UUID(body["api_key_id"]),
        provider=ProviderEnum(body["provider"]),
        model=body["model"],
        upstream_model=body["upstream_model"],
        tokens_in=body["tokens_in"],
        tokens_out=body["tokens_out"],
        # entries queued before the flag existed were all provider-reported
        tokens_estimated=body.get("tokens_estimated", False),
        cost=Decimal(body["cost"]),
        finish_reason=body["finish_reason"],
        request_id=body["request_id"],
        created_at=datetime.fromisoformat(body["created_at"]),
    )
    return record, body["attempts"]
