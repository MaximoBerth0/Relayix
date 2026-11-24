import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import UUID

import anyio

from app.core.accounting.registry import CounterRegistry
from app.core.accounting.usage_recorder import UsageRecorder
from app.core.adapters.registry import AdapterRegistry
from app.core.exceptions import (
    AdapterNotRegistered,
    UpstreamAmbiguous,
    UpstreamStreamInterrupted,
    UpstreamUnavailable,
)
from app.core.routing.router import RoutingService
from app.core.routing.strategies import Candidate
from app.infra.global_exceptions import ProviderNotAvailable
from app.models.domain.chat import ChatRequest, ChatResponse, ChatStreamEvent
from app.models.domain.enums import FailoverPolicy
from app.services.token_budget_service import TokenBudgetService

logger = logging.getLogger(__name__)


class GatewayService:
    def __init__(
        self,
        registry: AdapterRegistry,
        router: RoutingService,
        recorder: UsageRecorder,
        detached_recorder: UsageRecorder,
        counters: CounterRegistry,
        budget: TokenBudgetService | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._recorder = recorder
        # its own session: an abandoned stream can't rely on the request's transaction
        self._detached_recorder = detached_recorder
        self._counters = counters
        self._budget = budget

    async def complete(self, request: ChatRequest, api_key_id: UUID) -> ChatResponse:

        candidates = self._router.candidates_for(request.model)

        last_error: Exception | None = None

        ambiguous_error: UpstreamAmbiguous | None = None
        for candidate in candidates:
            try:
                adapter = self._registry.get(candidate.provider)
            except AdapterNotRegistered:
                # tier lists a provider this deployment didn't configure; skip it.
                logger.warning(
                    "no adapter configured for candidate provider",
                    extra={"tier": request.model, "provider": candidate.provider.value},
                )
                continue

            # send the provider its own concrete model name, not the tier alias.
            upstream = replace(request, model=candidate.model)
            logger.info(
                "routing completion",
                extra={
                    "tier": request.model,
                    "provider": candidate.provider.value,
                    "model": candidate.model,
                },
            )

            try:
                response = await adapter.complete(upstream)
            except UpstreamUnavailable as exc:
                logger.warning(
                    "candidate unavailable, failing over",
                    extra={"provider": candidate.provider.value, "model": candidate.model},
                    exc_info=exc,
                )
                last_error = exc
                continue
            except UpstreamAmbiguous as exc:
                # the request may already have run and billed. nobody knows
                last_error = exc
                ambiguous_error = exc
                if request.failover_policy is FailoverPolicy.AT_LEAST_ONCE:
                    logger.warning(
                        "candidate outcome unknown, failing over (at-least-once)",
                        extra={"provider": candidate.provider.value, "model": candidate.model},
                        exc_info=exc,
                    )
                    continue
                logger.error(
                    "candidate outcome unknown, not failing over (at-most-once)",
                    extra={"provider": candidate.provider.value, "model": candidate.model},
                    exc_info=exc,
                )
                raise

            logger.info(
                "completion done",
                extra={
                    "model": response.model,
                    "provider": response.provider.value,
                    "tokens_in": response.tokens_in,
                    "tokens_out": response.tokens_out,
                    "finish_reason": response.finish_reason,
                    "upstream_request_id": response.request_id,
                },
            )
            await self._bill(api_key_id, response, candidate.model)
            return response

        # Every candidate was either unconfigured or failed.
        logger.error(
            "no candidate served the request",
            extra={"tier": request.model, "candidates": len(candidates)},
            exc_info=last_error,
        )

        if ambiguous_error is not None:
            raise ambiguous_error

        raise ProviderNotAvailable() from last_error

    async def stream(
        self, request: ChatRequest, api_key_id: UUID
    ) -> AsyncIterator[ChatStreamEvent]:
        """Like `complete`, but streams incremental deltas.

        Candidates are tried the same way as `complete`: this method itself
        drives failover, but only up to the *first* event a candidate
        produces. Once a candidate has yielded anything, we've committed —
        content may already be on its way to the caller, so a failure past
        that point can no longer fail over to the next candidate. That means
        every failure this method itself can raise (before returning) behaves
        exactly like `complete`'s: a clean, pre-stream exception the HTTP
        layer renders as a normal JSON error. Only the returned generator can
        still fail, and only as `UpstreamStreamInterrupted`.
        """
        candidates = self._router.candidates_for(request.model)

        last_error: Exception | None = None
        ambiguous_error: UpstreamAmbiguous | None = None

        for candidate in candidates:
            try:
                adapter = self._registry.get(candidate.provider)
            except AdapterNotRegistered:
                logger.warning(
                    "no adapter configured for candidate provider",
                    extra={"tier": request.model, "provider": candidate.provider.value},
                )
                continue

            upstream = replace(request, model=candidate.model)
            logger.info(
                "routing streamed completion",
                extra={
                    "tier": request.model,
                    "provider": candidate.provider.value,
                    "model": candidate.model,
                },
            )

            stream_iter = adapter.stream(upstream)
            try:
                first_event = await anext(stream_iter)
            except StopAsyncIteration:
                # contract violation: an adapter must always end by yielding
                # a terminal ChatResponse. Treat as if it produced nothing.
                last_error = ProviderNotAvailable(
                    f"{candidate.provider.value} stream produced no events"
                )
                continue
            except UpstreamUnavailable as exc:
                logger.warning(
                    "candidate unavailable, failing over",
                    extra={"provider": candidate.provider.value, "model": candidate.model},
                    exc_info=exc,
                )
                last_error = exc
                continue
            except UpstreamAmbiguous as exc:
                last_error = exc
                ambiguous_error = exc
                if request.failover_policy is FailoverPolicy.AT_LEAST_ONCE:
                    logger.warning(
                        "candidate outcome unknown, failing over (at-least-once)",
                        extra={"provider": candidate.provider.value, "model": candidate.model},
                        exc_info=exc,
                    )
                    continue
                logger.error(
                    "candidate outcome unknown, not failing over (at-most-once)",
                    extra={"provider": candidate.provider.value, "model": candidate.model},
                    exc_info=exc,
                )
                raise

            # committed: this candidate has produced its first event.
            return self._drain(stream_iter, first_event, api_key_id, candidate, upstream)

        logger.error(
            "no candidate served the streamed request",
            extra={"tier": request.model, "candidates": len(candidates)},
            exc_info=last_error,
        )

        if ambiguous_error is not None:
            raise ambiguous_error

        raise ProviderNotAvailable() from last_error

    async def _drain(
        self,
        stream_iter: AsyncIterator[ChatStreamEvent],
        first_event: ChatStreamEvent,
        api_key_id: UUID,
        candidate: Candidate,
        request: ChatRequest,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Forward events from an already-committed stream, billing once it
        reaches its terminal ChatResponse (cleanly or via a partial usage
        record salvaged from a mid-stream interruption).

        A stream that ends any other way (the client disconnected, the task was
        cancelled) still had its tokens billed upstream, so what was forwarded
        is recorded as an errored partial on the way out.
        """
        model = candidate.model
        forwarded: list[str] = []
        billed = False
        try:
            event = first_event
            while True:
                if isinstance(event, ChatResponse):
                    logger.info(
                        "streamed completion done",
                        extra={
                            "model": event.model,
                            "provider": event.provider.value,
                            "tokens_in": event.tokens_in,
                            "tokens_out": event.tokens_out,
                            "finish_reason": event.finish_reason,
                            "upstream_request_id": event.request_id,
                        },
                    )
                    await self._bill(api_key_id, event, model)
                    billed = True
                    yield event
                    return
                forwarded.append(event.content)
                yield event
                event = await anext(stream_iter)
        except UpstreamStreamInterrupted as exc:
            if exc.partial is not None:
                partial = self._estimate_missing_usage(exc.partial, request)
                logger.warning(
                    "stream interrupted, billing partial usage",
                    extra={
                        "model": partial.model,
                        "provider": partial.provider.value,
                        "tokens_in": partial.tokens_in,
                        "tokens_out": partial.tokens_out,
                        "tokens_estimated": partial.tokens_estimated,
                    },
                    exc_info=exc,
                )
                await self._bill(api_key_id, partial, model)
                billed = True
            raise
        finally:
            if not billed and forwarded:
                await self._bill_abandoned(api_key_id, candidate, request, "".join(forwarded))

    async def _bill_abandoned(
        self, api_key_id: UUID, candidate: Candidate, request: ChatRequest, content: str
    ) -> None:
        """Token counts only arrive with the terminal event, so both are estimated.
        Shielded: the caller's task is usually being cancelled.
        """
        partial = ChatResponse(
            provider=candidate.provider,
            model=candidate.model,
            content=content,
            tokens_in=0,
            tokens_out=0,
            finish_reason="error",
            request_id="",
        )
        with anyio.CancelScope(shield=True):
            partial = self._estimate_missing_usage(partial, request)
            logger.warning(
                "stream abandoned before its terminal event, recording partial usage",
                extra={
                    "provider": candidate.provider.value,
                    "model": candidate.model,
                    "tokens_in": partial.tokens_in,
                    "tokens_out": partial.tokens_out,
                },
            )
            await self._bill(api_key_id, partial, candidate.model, self._detached_recorder)

    def _estimate_missing_usage(self, partial: ChatResponse, request: ChatRequest) -> ChatResponse:
        """Fill the counts a cut stream never received (OpenAI sends usage only in
        its last chunk, Anthropic its output count only at the end) and flag them.

        A prompt always has tokens and forwarded content always cost some, so a 0
        here means unknown, not free. Best effort: a counter failure keeps the 0s.
        """
        tokens_in, tokens_out = partial.tokens_in, partial.tokens_out
        if tokens_in and (tokens_out or not partial.content):
            return partial
        try:
            counter = self._counters.get(partial.provider)
            if not tokens_in:
                tokens_in = counter.count(request)
            if not tokens_out and partial.content:
                tokens_out = counter.count_completion(request.model, partial.content)
        except Exception:
            logger.exception(
                "token estimate failed, recording the partial with missing counts",
                extra={"provider": partial.provider.value, "model": request.model},
            )
            return partial
        return replace(partial, tokens_in=tokens_in, tokens_out=tokens_out, tokens_estimated=True)

    async def _bill(
        self,
        api_key_id: UUID,
        response: ChatResponse,
        model: str,
        recorder: UsageRecorder | None = None,
    ) -> None:
        """Record usage without ever failing the caller: the provider already billed,
        so a pricing or DB error here is ours to reconcile, not the caller's.
        """
        if self._budget is not None:
            await self._budget.settle(response)
        try:
            await (recorder or self._recorder).record(api_key_id, response, model)
        except Exception:
            logger.exception(
                "usage record failed after upstream billed",
                extra={
                    "api_key_id": str(api_key_id),
                    "provider": response.provider.value,
                    "model": model,
                    "upstream_model": response.model,
                    "tokens_in": response.tokens_in,
                    "tokens_out": response.tokens_out,
                    "finish_reason": response.finish_reason,
                    "upstream_request_id": response.request_id,
                },
            )
