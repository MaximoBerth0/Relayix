from __future__ import annotations

from app.core.routing.strategies import Candidate, PriorityStrategy, RoutingStrategy
from app.infra.global_exceptions import ProviderNotAvailable
from app.models.domain.enums import ProviderEnum

# routing catalog: maps a caller-facing routing key (a capability "tier") to the
# concrete (provider, model) candidates that can serve it. 
DEFAULT_CATALOG: dict[str, list[Candidate]] = {
    "fast": [
        Candidate(ProviderEnum.OPENAI, "gpt-4o-mini"),
        Candidate(ProviderEnum.ANTHROPIC, "claude-3-5-haiku-latest"),
    ],
    "smart": [
        Candidate(ProviderEnum.OPENAI, "gpt-4o"),
        Candidate(ProviderEnum.ANTHROPIC, "claude-3-5-sonnet-latest"),
    ],
}

# default provider preference for PriorityStrategy, most-preferred first.
DEFAULT_PRIORITY: list[ProviderEnum] = [ProviderEnum.OPENAI, ProviderEnum.ANTHROPIC]


class RoutingService:
    """Turns a routing key into an ordered list of candidates to try.
    """

    def __init__(
        self,
        catalog: dict[str, list[Candidate]],
        strategy: RoutingStrategy,
    ) -> None:
        self._catalog = catalog
        self._strategy = strategy

    def candidates_for(self, model: str) -> list[Candidate]:
        """Ranked candidates for a routing tier, best first.
        Raises ProviderNotAvailable when the tier is unknown.
        """
        candidates = self._catalog.get(model)
        if not candidates:
            raise ProviderNotAvailable(f"unknown routing tier {model!r}")
        return self._strategy.rank(candidates)


def build_router(
    catalog: dict[str, list[Candidate]] | None = None,
    priority: list[ProviderEnum] | None = None,
) -> RoutingService:
    """Construct the process-wide RoutingService with the priority strategy."""
    return RoutingService(
        catalog=catalog if catalog is not None else DEFAULT_CATALOG,
        strategy=PriorityStrategy(priority if priority is not None else DEFAULT_PRIORITY),
    )
