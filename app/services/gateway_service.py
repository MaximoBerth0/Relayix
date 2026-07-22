import logging
from dataclasses import replace
from uuid import UUID

from app.core.adapters.registry import AdapterRegistry
from app.core.exceptions import (
    AdapterNotRegistered,
    UpstreamAmbiguous,
    UpstreamUnavailable,
)
from app.core.accounting.usage_recorder import UsageRecorder
from app.core.routing.router import RoutingService
from app.infra.global_exceptions import ProviderNotAvailable
from app.models.domain.chat import ChatRequest, ChatResponse
from app.models.domain.enums import FailoverPolicy

logger = logging.getLogger(__name__)


class GatewayService:
    def __init__(
        self,
        registry: AdapterRegistry,
        router: RoutingService,
        recorder: UsageRecorder,
    ) -> None:
        self._registry = registry
        self._router = router
        self._recorder = recorder

    async def complete(self, request: ChatRequest, api_key_id: UUID) -> ChatResponse:

        candidates = self._router.candidates_for(request.model)

        last_error: Exception | None = None
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
            await self._recorder.record(api_key_id, response)
            return response

        # Every candidate was either unconfigured or failed.
        raise ProviderNotAvailable() from last_error
