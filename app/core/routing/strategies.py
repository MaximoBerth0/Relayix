from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.domain.enums import ProviderEnum


@dataclass(frozen=True)
class Candidate:
    provider: ProviderEnum
    model: str


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
