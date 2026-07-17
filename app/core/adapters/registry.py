from app.core.adapters.anthropic_adapter import AnthropicAdapter
from app.core.adapters.base import ProviderAdapter
from app.core.adapters.openai_adapter import OpenAIAdapter
from app.core.exceptions import AdapterNotRegistered
from app.core.resilience.circuit_breaker import CircuitBreaker
from app.core.resilience.resilient_adapter import ResilientAdapter
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
            raise AdapterNotRegistered(
                f"no adapter registered for provider {provider!r}"
            ) from None


def _with_resilience(
    inner: ProviderAdapter, provider: ProviderEnum, config: Settings
) -> ResilientAdapter:
    """Wrap an adapter with its own per-provider breaker and a hard timeout."""
    breaker = CircuitBreaker(
        fail_threshold=config.circuit_breaker_fail_threshold,
        reset_timeout_s=config.circuit_breaker_reset_timeout_s,
    )
    return ResilientAdapter(
        inner=inner,
        provider=provider,
        breaker=breaker,
        timeout_s=config.provider_timeout_s,
    )


def build_registry(config: Settings = settings) -> AdapterRegistry:
    """Construct the registry from configured credentials.
    a provider is only registered when its API key is set, so a deployment can
    run with just one provider configured.
    """
    registry = AdapterRegistry()

    if config.openai_api_key:
        adapter = OpenAIAdapter(api_key=config.openai_api_key, timeout=config.provider_timeout_s)
        registry.register(
            ProviderEnum.OPENAI,
            _with_resilience(adapter, ProviderEnum.OPENAI, config),
        )

    if config.anthropic_api_key:
        adapter = AnthropicAdapter(api_key=config.anthropic_api_key, timeout=config.provider_timeout_s)
        registry.register(
            ProviderEnum.ANTHROPIC,
            _with_resilience(adapter, ProviderEnum.ANTHROPIC, config),
        )

    return registry
