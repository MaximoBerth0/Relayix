import logging

from app.core.adapters.registry import AdapterRegistry
from app.infra.global_exceptions import ProviderNotAvailable
from app.models.domain.chat import ChatRequest, ChatResponse
from app.models.domain.enums import ProviderEnum

logger = logging.getLogger(__name__)


class GatewayService:
    def __init__(self, registry: AdapterRegistry) -> None:
        self._registry = registry

    async def complete(self, request: ChatRequest) -> ChatResponse:
        provider = self._resolve_provider(request.model)
        logger.info(
            "routing completion",
            extra={"model": request.model, "provider": provider.value},
        )

        try:
            adapter = self._registry.get(provider)
        except KeyError:
            logger.warning(
                "no adapter configured for provider",
                extra={"model": request.model, "provider": provider.value},
            )
            raise ProviderNotAvailable() from None

        response = await adapter.complete(request)
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
        return response

    def _resolve_provider(self, model: str) -> ProviderEnum:
        if model.startswith("claude-"):
            return ProviderEnum.ANTHROPIC
        return ProviderEnum.OPENAI
