from app.core.adapters.anthropic_adapter import AnthropicAdapter
from app.core.adapters.base import ProviderAdapter
from app.core.adapters.openai_adapter import OpenAIAdapter
from app.infra.config import Settings, settings
from app.models.domain.enums import ProviderEnum


class AdapterRegistry:
    """Holds one adapter instance per provider"""

    def __init__(self) -> None:
        self._adapters: dict[ProviderEnum, ProviderAdapter] = {}

    def register(self, provider: ProviderEnum, adapter: ProviderAdapter) -> None:
        self._adapters[provider] = adapter

    def get(self, provider: ProviderEnum) -> ProviderAdapter:
        try:
            return self._adapters[provider]
        except KeyError:
            raise KeyError(f"no adapter registered for provider {provider!r}") from None


def build_registry(config: Settings = settings) -> AdapterRegistry:
    """Construct the registry from configured credentials.

    A provider is only registered when its API key is set, so a deployment can
    run with just one provider configured.
    """
    registry = AdapterRegistry()

    if config.openai_api_key:
        registry.register(
            ProviderEnum.OPENAI,
            OpenAIAdapter(api_key=config.openai_api_key, timeout=config.provider_timeout_s),
        )

    if config.anthropic_api_key:
        registry.register(
            ProviderEnum.ANTHROPIC,
            AnthropicAdapter(api_key=config.anthropic_api_key, timeout=config.provider_timeout_s),
        )

    return registry
