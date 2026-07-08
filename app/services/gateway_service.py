from app.core.adapters.registry import AdapterRegistry
from app.models.domain.chat import ChatRequest, ChatResponse
from app.models.domain.enums import ProviderEnum

class GatewayService: 
    def __init__(self, registry: AdapterRegistry): ...

    async def complete(self, request: ChatRequest) -> ChatResponse:
        provider = self._resolve_provider(request.model)
        adapter = self._registry.get(provider)
        return await adapter.complete(request)

    def _resolve_provider(self, model: str) -> ProviderEnum:
        if model.startswith("claude-"):
            return ProviderEnum.ANTHROPIC
        return ProviderEnum.OPENAI

