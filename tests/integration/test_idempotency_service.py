"""IdempotencyService + RedisIdempotencyStore wired together against real
Redis (no HTTP, no DB): same-key conflicts, concurrent in-flight races, and
the replay / release / ambiguous-outcome paths.
"""

import asyncio
from uuid import uuid4

import pytest

from app.core.exceptions import UpstreamAmbiguous
from app.infra.config import settings
from app.infra.idempotency.redis_store import RedisIdempotencyStore
from app.models.domain.chat import ChatRequest, ChatResponse, Message
from app.models.domain.enums import ProviderEnum
from app.services.exceptions import (
    IdempotencyInProgress,
    IdempotencyKeyConflict,
    IdempotencyOutcomeUnknown,
)
from app.services.idempotency_service import IdempotencyService

REQUEST = ChatRequest(model="gpt-4o", messages=[Message(role="user", content="hi")])
OTHER_REQUEST = ChatRequest(model="gpt-4o", messages=[Message(role="user", content="bye")])

RESPONSE = ChatResponse(
    provider=ProviderEnum.OPENAI,
    model="gpt-4o",
    content="hi back",
    tokens_in=1,
    tokens_out=1,
    finish_reason="stop",
    request_id="req-1",
)


def service(redis_client) -> IdempotencyService:
    store = RedisIdempotencyStore(
        redis_client,
        inflight_ttl_s=settings.idempotency_inflight_ttl_s,
        completed_ttl_s=settings.idempotency_ttl_s,
    )
    return IdempotencyService(store)


async def test_a_fresh_key_executes_the_operation_exactly_once(redis_client):
    svc = service(redis_client)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return RESPONSE

    response = await svc.execute(key="k1", api_key_id=uuid4(), request=REQUEST, operation=operation)

    assert response == RESPONSE
    assert calls == 1


async def test_replaying_the_same_key_and_body_returns_the_stored_response_without_re_executing(
    redis_client,
):
    svc = service(redis_client)
    api_key_id = uuid4()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return RESPONSE

    await svc.execute(key="k1", api_key_id=api_key_id, request=REQUEST, operation=operation)
    replayed = await svc.execute(key="k1", api_key_id=api_key_id, request=REQUEST, operation=operation)

    assert replayed == RESPONSE
    assert calls == 1  # the second call never ran the operation


async def test_reusing_a_key_with_a_different_body_raises_a_conflict(redis_client):
    svc = service(redis_client)
    api_key_id = uuid4()

    async def operation():
        return RESPONSE

    await svc.execute(key="k1", api_key_id=api_key_id, request=REQUEST, operation=operation)

    with pytest.raises(IdempotencyKeyConflict):
        await svc.execute(key="k1", api_key_id=api_key_id, request=OTHER_REQUEST, operation=operation)


async def test_the_same_key_string_is_independent_across_api_keys(redis_client):
    """Two tenants reusing the same literal Idempotency-Key must not see each
    other's reservation: the store scopes by api_key_id.
    """
    svc = service(redis_client)

    async def operation():
        return RESPONSE

    await svc.execute(key="shared", api_key_id=uuid4(), request=REQUEST, operation=operation)
    response = await svc.execute(key="shared", api_key_id=uuid4(), request=REQUEST, operation=operation)

    assert response == RESPONSE


async def test_a_concurrent_request_for_the_same_in_flight_key_is_told_to_wait(redis_client):
    svc = service(redis_client)
    api_key_id = uuid4()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_operation():
        started.set()
        await release.wait()
        return RESPONSE

    async def fast_operation():
        return RESPONSE

    first = asyncio.ensure_future(
        svc.execute(key="k1", api_key_id=api_key_id, request=REQUEST, operation=slow_operation)
    )
    await started.wait()

    with pytest.raises(IdempotencyInProgress):
        await svc.execute(key="k1", api_key_id=api_key_id, request=REQUEST, operation=fast_operation)

    release.set()
    await first


async def test_an_ambiguous_operation_failure_marks_the_key_and_is_not_retried_automatically(
    redis_client,
):
    svc = service(redis_client)
    api_key_id = uuid4()

    async def operation():
        raise UpstreamAmbiguous("provider outcome unknown")

    with pytest.raises(UpstreamAmbiguous):
        await svc.execute(key="k1", api_key_id=api_key_id, request=REQUEST, operation=operation)

    with pytest.raises(IdempotencyOutcomeUnknown):
        await svc.execute(key="k1", api_key_id=api_key_id, request=REQUEST, operation=operation)


async def test_a_clean_operation_failure_releases_the_key_for_a_retry(redis_client):
    """An error that isn't UpstreamAmbiguous means the request provably never
    executed, so the key must be released and a retry must run cleanly.
    """
    svc = service(redis_client)
    api_key_id = uuid4()
    attempts = 0

    async def flaky_operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("boom")
        return RESPONSE

    with pytest.raises(ValueError):
        await svc.execute(key="k1", api_key_id=api_key_id, request=REQUEST, operation=flaky_operation)

    response = await svc.execute(key="k1", api_key_id=api_key_id, request=REQUEST, operation=flaky_operation)

    assert response == RESPONSE
    assert attempts == 2


# streaming


async def test_execute_stream_replays_a_completed_response_as_a_one_shot_stream(redis_client):
    svc = service(redis_client)
    api_key_id = uuid4()

    async def make_stream():
        async def gen():
            yield RESPONSE

        return gen()

    primed = await svc.execute_stream(
        key="k1", api_key_id=api_key_id, request=REQUEST, operation=make_stream
    )
    first_events = [event async for event in primed]
    assert first_events[-1] == RESPONSE

    replayed = await svc.execute_stream(
        key="k1", api_key_id=api_key_id, request=REQUEST, operation=make_stream
    )
    replayed_events = [event async for event in replayed]
    assert replayed_events[-1] == RESPONSE
