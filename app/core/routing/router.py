from __future__ import annotations

from app.core.routing.strategies import (
    Candidate,
    CostStrategy,
    ModelMetadata,
    PriorityStrategy,
    ReasoningStrategy,
    RoutingStrategy,
)
from app.infra.global_exceptions import ProviderNotAvailable
from app.models.domain.enums import ProviderEnum

# routing catalog: maps a caller-facing routing key (a capability "tier") to the
# concrete (provider, model) candidates that can serve it. 
DEFAULT_CATALOG: dict[str, list[Candidate]] = {
    "default": [
        Candidate(ProviderEnum.OPENAI, "gpt-4o"),
        Candidate(ProviderEnum.ANTHROPIC, "claude-sonnet-5"),
    ],
    "reasoning": [
        Candidate(ProviderEnum.OPENAI, "o3"),
        Candidate(ProviderEnum.ANTHROPIC, "claude-opus-4-8"),
    ],
    "low_cost": [
        Candidate(ProviderEnum.OPENAI, "gpt-4o-mini"),
        Candidate(ProviderEnum.ANTHROPIC, "claude-haiku-4-5"),
    ],
}

# Costs are USD per 1K tokens. 
# Relative heuristic score (0-100).
# Used only to compare models inside RoutingStrategy.
MODEL_REGISTRY: dict[str, ModelMetadata] = {
    # OpenAI
    "gpt-4o": ModelMetadata(ProviderEnum.OPENAI, 0.0025, 0.010, reasoning_score=80, latency_score=70),
    "gpt-4o-mini": ModelMetadata(ProviderEnum.OPENAI, 0.00015, 0.0006, reasoning_score=55, latency_score=95),
    "o3": ModelMetadata(ProviderEnum.OPENAI, 0.010, 0.040, reasoning_score=98, latency_score=30),
    # Anthropic
    "claude-sonnet-5": ModelMetadata(ProviderEnum.ANTHROPIC, 0.003, 0.015, reasoning_score=88, latency_score=68),
    "claude-opus-4-8": ModelMetadata(ProviderEnum.ANTHROPIC, 0.005, 0.025, reasoning_score=97, latency_score=45),
    "claude-haiku-4-5": ModelMetadata(ProviderEnum.ANTHROPIC, 0.001, 0.005, reasoning_score=70, latency_score=92),
}

# default provider preference for PriorityStrategy, most-preferred first.
DEFAULT_PRIORITY: list[ProviderEnum] = [ProviderEnum.OPENAI, ProviderEnum.ANTHROPIC]


def validate_registry(
    catalog: dict[str, list[Candidate]],
    registry: dict[str, ModelMetadata],
) -> None:
    """Fail fast if the catalog and registry disagree"""
    for tier, candidates in catalog.items():
        for candidate in candidates:
            meta = registry.get(candidate.model)
            if meta is None:
                raise ValueError(
                    f"tier {tier!r}: model {candidate.model!r} has no entry in MODEL_REGISTRY"
                )
            if meta.provider != candidate.provider:
                raise ValueError(
                    f"tier {tier!r}: model {candidate.model!r} is registered under "
                    f"{meta.provider} but the catalog lists it under {candidate.provider}"
                )


class RoutingService:
    """Turns a routing key into an ordered list of candidates to try.
each tier is ranked by its own strategy 
    """

    def __init__(
        self,
        catalog: dict[str, list[Candidate]],
        strategies: dict[str, RoutingStrategy],
        default_strategy: RoutingStrategy,
    ) -> None:
        self._catalog = catalog
        self._strategies = strategies
        self._default_strategy = default_strategy

    def candidates_for(self, model: str) -> list[Candidate]:
        """ranked candidates for a routing tier, best first.
        Raises ProviderNotAvailable when the tier is unknown.
        """
        candidates = self._catalog.get(model)
        if not candidates:
            raise ProviderNotAvailable(f"unknown routing tier {model!r}")
        strategy = self._strategies.get(model, self._default_strategy)
        return strategy.rank(candidates)


def build_router(
    catalog: dict[str, list[Candidate]] | None = None,
    priority: list[ProviderEnum] | None = None,
    registry: dict[str, ModelMetadata] | None = None,
    strategies: dict[str, RoutingStrategy] | None = None,
) -> RoutingService:
    """Construct the process-wide RoutingService.

    Each tier is ranked by the strategy that matches its intent: reasoning by
    reasoning score, low_cost by price. Any other tier (e.g. default) falls back
    to provider preference. Pass `strategies` to override the per-tier map.
    """
    catalog = catalog if catalog is not None else DEFAULT_CATALOG
    registry = registry if registry is not None else MODEL_REGISTRY
    validate_registry(catalog, registry)

    default_strategy = PriorityStrategy(priority if priority is not None else DEFAULT_PRIORITY)
    if strategies is None:
        strategies = {
            "reasoning": ReasoningStrategy(registry),
            "low_cost": CostStrategy(registry),
        }
    return RoutingService(
        catalog=catalog,
        strategies=strategies,
        default_strategy=default_strategy,
    )
