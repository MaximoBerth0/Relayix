"""Idempotency as an application service, invoked from the HTTP layer"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol
from uuid import UUID

from app.core.exceptions import UpstreamAmbiguous
from app.models.domain.chat import (
    ChatRequest,
    ChatResponse,
    ChatStreamDelta,
    ChatStreamEvent,
)
from app.models.domain.enums import IdempotencyStatus, ProviderEnum
from app.models.domain.idempotency import ReservationOutcome
from app.services.exceptions import (
    IdempotencyInProgress,
    IdempotencyKeyConflict,
    IdempotencyOutcomeUnknown,
)


class IdempotencyStore(Protocol):
    """Persistence port for idempotency records, implemented in the infra layer"""

    async def reserve(
        self, api_key_id: UUID, key: str, fingerprint: str
    ) -> ReservationOutcome:
        """Atomically claim the key, or report the existing record if taken."""
        ...

    async def complete(
        self, api_key_id: UUID, key: str, fingerprint: str, response_body: dict
    ) -> None:
        """Store the final response and mark the record completed."""
        ...

    async def mark_ambiguous(
        self, api_key_id: UUID, key: str, fingerprint: str, error: str
    ) -> None:
        """Park the record in a terminal ambiguous state (best effort)."""
        ...

    async def release(self, api_key_id: UUID, key: str) -> None:
        """Drop an in-flight claim so the caller can retry (best effort)."""
        ...


Operation = Callable[[], Awaitable[ChatResponse]]
StreamOperation = Callable[[], Awaitable[AsyncIterator[ChatStreamEvent]]]


class IdempotencyService:
    """Wraps an operation so it runs at most once per (api_key, idempotency key)"""

    def __init__(self, store: IdempotencyStore) -> None:
        self._store = store

    async def execute(
        self,
        *,
        key: str,
        api_key_id: UUID,
        request: ChatRequest,
        operation: Operation,
    ) -> ChatResponse:
        fingerprint = self._fingerprint(request)
        outcome = await self._store.reserve(api_key_id, key, fingerprint)

        if outcome.acquired:
            # we own the slot: run the real work exactly once, then persist it.
            try:
                response = await operation()
            except UpstreamAmbiguous as exc:
                # the provider may already have executed and billed this request
                await self._store.mark_ambiguous(
                    api_key_id, key, fingerprint, error=str(exc)
                )
                raise
            except BaseException:
                # the request probably never executed (or we were cancelled)
                await self._store.release(api_key_id, key)
                raise
            await self._store.complete(
                api_key_id, key, fingerprint, self._serialize(response)
            )
            return response

        # someone already claimed this key.
        if outcome.request_fingerprint != fingerprint:
            # same key, different body so its client bug
            raise IdempotencyKeyConflict()

        if outcome.status == IdempotencyStatus.COMPLETED.value:
            return self._deserialize(outcome.response_body or {})

        if outcome.status == IdempotencyStatus.AMBIGUOUS.value:
            raise IdempotencyOutcomeUnknown(outcome.error)

        # still in flight: the original request hasn't finished yet.
        raise IdempotencyInProgress()

    async def execute_stream(
        self,
        *,
        key: str,
        api_key_id: UUID,
        request: ChatRequest,
        operation: StreamOperation,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Streaming counterpart to `execute`.

        `operation` is expected to behave like `GatewayService.stream`: it
        does its own failover internally and only returns once a candidate has
        actually committed to producing output (or raises before anything is
        sent anywhere).
        """
        fingerprint = self._fingerprint(request)
        outcome = await self._store.reserve(api_key_id, key, fingerprint)

        if outcome.acquired:
            try:
                primed = await operation()
            except UpstreamAmbiguous as exc:
                await self._store.mark_ambiguous(
                    api_key_id, key, fingerprint, error=str(exc)
                )
                raise
            except BaseException:
                await self._store.release(api_key_id, key)
                raise
            return self._settle(primed, key, api_key_id, fingerprint)

        if outcome.request_fingerprint != fingerprint:
            raise IdempotencyKeyConflict()

        if outcome.status == IdempotencyStatus.COMPLETED.value:
            return self._replay_stream(outcome.response_body or {})

        if outcome.status == IdempotencyStatus.AMBIGUOUS.value:
            raise IdempotencyOutcomeUnknown(outcome.error)

        raise IdempotencyInProgress()

    async def _settle(
        self,
        primed: AsyncIterator[ChatStreamEvent],
        key: str,
        api_key_id: UUID,
        fingerprint: str,
    ) -> AsyncIterator[ChatStreamEvent]:
        try:
            async for event in primed:
                yield event
                if isinstance(event, ChatResponse):
                    await self._store.complete(
                        api_key_id, key, fingerprint, self._serialize(event)
                    )
        except BaseException as exc:
            # a break here (provider error, client
            # disconnect, cancellation) is never safe to release for retry.
            await self._store.mark_ambiguous(
                api_key_id, key, fingerprint, error=str(exc)
            )
            raise
        finally:
            await primed.aclose()

    async def _replay_stream(self, body: dict) -> AsyncIterator[ChatStreamEvent]:
        """Re-serve a completed request's stored response as a one-shot stream."""
        response = self._deserialize(body)
        if response.content:
            yield ChatStreamDelta(content=response.content)
        yield response

    @staticmethod
    def _fingerprint(request: ChatRequest) -> str:
        """A stable hash of the request body, everything that shapes the output"""
        payload = {
            "model": request.model,
            "messages": [
                {"role": m.role, "content": m.content} for m in request.messages
            ],
            "max_tokens": request.max_tokens,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _serialize(response: ChatResponse) -> dict:
        return {
            "provider": response.provider.value,
            "model": response.model,
            "content": response.content,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "finish_reason": response.finish_reason,
            "request_id": response.request_id,
        }

    @staticmethod
    def _deserialize(body: dict) -> ChatResponse:
        return ChatResponse(
            provider=ProviderEnum(body["provider"]),
            model=body["model"],
            content=body["content"],
            tokens_in=body["tokens_in"],
            tokens_out=body["tokens_out"],
            finish_reason=body["finish_reason"],
            request_id=body["request_id"],
        )
