"""UsageRetryService + RedisUsageQueue against real Redis and Postgres: the
worker side of usage inserts that failed in the request path.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from app.infra.usage_queue.redis_queue import (
    DEAD_KEY,
    PENDING_KEY,
    PROCESSING_KEY,
    RedisUsageQueue,
)
from app.models.db.usage_record import Usage_Record
from app.models.domain.enums import ProviderEnum
from app.models.domain.usage_record import UsageRecord
from app.repositories.usage_repo import UsageRepo
from app.services.usage_retry_service import RetryOutcome, UsageRetryService


def make_record(api_key_id) -> UsageRecord:
    return UsageRecord.create(
        api_key_id=api_key_id,
        provider=ProviderEnum.OPENAI,
        model="gpt-4o",
        tokens_in=12,
        tokens_out=8,
        cost=Decimal("0.00011"),
        finish_reason="stop",
        request_id="chatcmpl-1",
        upstream_model="gpt-4o-2024-08-06",
    )


async def failing_persist(record) -> None:
    raise ConnectionError("db down")


async def test_a_queued_record_is_persisted_exactly_as_it_was_built(
    db_session, seed_api_key, redis_client
):
    queue = RedisUsageQueue(redis_client)
    original = make_record(seed_api_key.id)
    await queue.push(original)

    outcome = await UsageRetryService(queue, UsageRepo(db_session).save, 3).process_next(0.1)

    row = await db_session.scalar(select(Usage_Record))
    assert outcome is RetryOutcome.SAVED
    assert row.id == original.id
    assert row.cost == original.cost
    assert row.upstream_model == "gpt-4o-2024-08-06"
    assert row.created_at == original.created_at
    assert await redis_client.llen(PENDING_KEY) == 0
    assert await redis_client.llen(PROCESSING_KEY) == 0


async def test_persisting_the_same_record_twice_inserts_it_once(
    db_session, seed_api_key, usage_count
):
    """A worker that crashes after the commit but before the ack processes the
    entry again. The insert dedupes on the record id.
    """
    repo = UsageRepo(db_session)
    record = make_record(seed_api_key.id)

    await repo.save(record)
    await repo.save(record)

    assert await usage_count() == 1


async def test_a_failed_attempt_goes_back_to_pending_with_one_more_attempt(redis_client):
    queue = RedisUsageQueue(redis_client)
    api_key_id = uuid4()
    await queue.push(make_record(api_key_id))

    outcome = await UsageRetryService(queue, failing_persist, 3).process_next(0.1)

    entry = await queue.claim(0.1)
    assert outcome is RetryOutcome.RETRY
    assert entry.attempts == 1
    assert entry.record.api_key_id == api_key_id


async def test_an_entry_that_keeps_failing_ends_in_dead(redis_client):
    queue = RedisUsageQueue(redis_client)
    await queue.push(make_record(uuid4()))
    service = UsageRetryService(queue, failing_persist, max_attempts=2)

    outcomes = [await service.process_next(0.1) for _ in range(3)]

    assert outcomes == [RetryOutcome.RETRY, RetryOutcome.DEAD, RetryOutcome.EMPTY]
    assert await redis_client.llen(DEAD_KEY) == 1
    assert await redis_client.llen(PENDING_KEY) == 0
    assert await redis_client.llen(PROCESSING_KEY) == 0


async def test_entries_left_in_processing_by_a_crash_are_requeued(redis_client):
    queue = RedisUsageQueue(redis_client)
    await queue.push(make_record(uuid4()))
    await queue.claim(0.1)  # claimed, then the worker died before ack

    moved = await queue.requeue_processing()

    assert moved == 1
    assert await redis_client.llen(PROCESSING_KEY) == 0
    assert await queue.claim(0.1) is not None


async def test_an_empty_queue_returns_empty(redis_client):
    service = UsageRetryService(RedisUsageQueue(redis_client), failing_persist, 3)

    assert await service.process_next(0.1) is RetryOutcome.EMPTY


async def test_created_at_survives_the_queue_round_trip(redis_client):
    """The worker may persist hours later: the record keeps the request's time."""
    queue = RedisUsageQueue(redis_client)
    record = make_record(uuid4())
    await queue.push(record)

    entry = await queue.claim(0.1)

    assert entry.record == record
    assert entry.record.created_at < datetime.now(UTC)


async def test_the_estimated_flag_survives_the_queue_round_trip(redis_client):
    queue = RedisUsageQueue(redis_client)
    await queue.push(replace(make_record(uuid4()), tokens_estimated=True))

    entry = await queue.claim(0.1)

    assert entry.record.tokens_estimated is True


async def test_an_entry_queued_before_the_estimated_flag_reads_as_reported(redis_client):
    queue = RedisUsageQueue(redis_client)
    await queue.push(make_record(uuid4()))
    raw = await redis_client.rpop(PENDING_KEY)
    body = json.loads(raw)
    del body["tokens_estimated"]
    await redis_client.lpush(PENDING_KEY, json.dumps(body))

    entry = await queue.claim(0.1)

    assert entry.record.tokens_estimated is False
