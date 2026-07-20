from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.domain.enums import ProviderEnum


@dataclass(frozen=True)
class Candidate:
    provider: ProviderEnum
    model: str


@dataclass(frozen=True)
class ModelMetadata:
    provider: ProviderEnum
    input_cost: float  # USD per 1K input tokens
    output_cost: float  # USD per 1K output tokens
    reasoning_score: int  # higher = smarter
    latency_score: int  # higher = faster


class RoutingStrategy(ABC):
    @abstractmethod
    def rank(self, candidates: list[Candidate]) -> list[Candidate]:
        """return `candidates` reordered by preference (best first)"""
        ...


class PriorityStrategy(RoutingStrategy):
    """static preference: order candidates by a fixed provider ranking
    """

    def __init__(self, order: list[ProviderEnum]) -> None:
        self._rank = {provider: index for index, provider in enumerate(order)}
        # lower number == higher priority
    def rank(self, candidates: list[Candidate]) -> list[Candidate]:
        last = len(self._rank) 
        return sorted(candidates, key=lambda c: self._rank.get(c.provider, last))


class MetadataStrategy(RoutingStrategy, ABC):
    """base for strategies that rank by a single field of a model's metadata.
    """

    _higher_is_better: bool = False

    def __init__(self, registry: dict[str, ModelMetadata]) -> None:
        self._registry = registry

    @abstractmethod
    def _metric(self, meta: ModelMetadata) -> float:
        """extract the value to rank on from a model's metadata"""
        ...

    def rank(self, candidates: list[Candidate]) -> list[Candidate]:
        worst = float("-inf") if self._higher_is_better else float("inf")

        def key(candidate: Candidate) -> float:
            meta = self._registry.get(candidate.model)
            return worst if meta is None else self._metric(meta)

        return sorted(candidates, key=key, reverse=self._higher_is_better)


# based on price-tokens
class CostStrategy(MetadataStrategy):
    """order candidates by total token cost, cheapest first."""

    def _metric(self, meta: ModelMetadata) -> float:
        return meta.input_cost + meta.output_cost


# based on reasoning capability
class ReasoningStrategy(MetadataStrategy):
    """order candidates by reasoning score, highest first"""

    _higher_is_better = True

    def _metric(self, meta: ModelMetadata) -> float:
        return meta.reasoning_score


# based on expected latency
class LatencyStrategy(MetadataStrategy):
    """order candidates by latency score, fastest first"""

    _higher_is_better = True

    def _metric(self, meta: ModelMetadata) -> float:
        return meta.latency_score
