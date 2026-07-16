from app.core.accounting.anthropic_counter import AnthropicCounter
from app.core.accounting.openai_counter import OpenAICounter
from app.core.accounting.token_counter import TokenCounter
from app.core.exceptions import CounterNotRegistered
from app.models.domain.enums import ProviderEnum


class CounterRegistry:
    """Holds one token counter instance per provider"""

    def __init__(self) -> None:
        self._counters: dict[ProviderEnum, TokenCounter] = {}

    def register(self, provider: ProviderEnum, counter: TokenCounter) -> None:
        self._counters[provider] = counter

    def get(self, provider: ProviderEnum) -> TokenCounter:
        try:
            return self._counters[provider]
        except KeyError:
            raise CounterNotRegistered(
                f"no counter registered for provider {provider!r}"
            ) from None


def build_registry() -> CounterRegistry:
    """Construct the registry with a counter for every provider.
    """
    registry = CounterRegistry()
    registry.register(ProviderEnum.OPENAI, OpenAICounter())
    registry.register(ProviderEnum.ANTHROPIC, AnthropicCounter())
    return registry
