"""Tier resolution: turning a caller-facing routing key into an ordered list of
concrete (provider, model) candidates"""

import pytest

from app.core.routing.router import (
    DEFAULT_CATALOG,
    DEFAULT_PRIORITY,
    MODEL_REGISTRY,
    RoutingService,
    build_router,
    validate_registry,
)
from app.core.routing.strategies import (
    Candidate,
    CostStrategy,
    LatencyStrategy,
    ModelMetadata,
    PriorityStrategy,
    ReasoningStrategy,
)
from app.infra.global_exceptions import ProviderNotAvailable
from app.models.domain.enums import ProviderEnum

OPENAI = ProviderEnum.OPENAI
ANTHROPIC = ProviderEnum.ANTHROPIC


# Resolution through the default router


def test_default_tier_ranks_by_provider_priority():
    """'default' has no dedicated strategy, so it falls back to PriorityStrategy
    and follows DEFAULT_PRIORITY (OpenAI first).
    """
    router = build_router()

    candidates = router.candidates_for("default")

    assert [c.provider for c in candidates] == [OPENAI, ANTHROPIC]
    assert [c.model for c in candidates] == ["gpt-4o", "claude-sonnet-5"]


def test_reasoning_tier_ranks_by_reasoning_score():
    """o3 (98) outranks claude-opus-4-8 (97), regardless of provider priority."""
    router = build_router()

    candidates = router.candidates_for("reasoning")

    assert [c.model for c in candidates] == ["o3", "claude-opus-4-8"]


def test_low_cost_tier_ranks_by_total_token_cost():
    """gpt-4o-mini totals 0.00075/1K vs claude-haiku-4-5 at 0.006/1K."""
    router = build_router()

    candidates = router.candidates_for("low_cost")

    assert [c.model for c in candidates] == ["gpt-4o-mini", "claude-haiku-4-5"]


def test_resolution_returns_every_candidate_not_just_the_winner():
    """Failover depends on the full list surviving ranking."""
    router = build_router()

    for tier, configured in DEFAULT_CATALOG.items():
        candidates = router.candidates_for(tier)
        assert len(candidates) == len(configured)
        assert set(candidates) == set(configured)


def test_unknown_tier_raises_provider_not_available():
    router = build_router()

    with pytest.raises(ProviderNotAvailable):
        router.candidates_for("does-not-exist")


def test_a_concrete_model_name_is_not_a_tier():
    """The catalog is keyed by capability tier, not by model id: asking for
    'gpt-4o' directly is an unknown tier.
    """
    router = build_router()

    with pytest.raises(ProviderNotAvailable):
        router.candidates_for("gpt-4o")


def test_resolution_is_repeatable():
    """Same key, same order, every call, no state carried between resolutions."""
    router = build_router()

    assert router.candidates_for("reasoning") == router.candidates_for("reasoning")


def test_empty_tier_is_treated_as_unavailable():
    """A tier configured with no candidates is unroutable rather than an empty
    success.
    """
    router = build_router(catalog={"empty": []}, registry={})

    with pytest.raises(ProviderNotAvailable):
        router.candidates_for("empty")


# build_router wiring


def test_custom_priority_flips_the_default_tier():
    router = build_router(priority=[ANTHROPIC, OPENAI])

    candidates = router.candidates_for("default")

    assert [c.provider for c in candidates] == [ANTHROPIC, OPENAI]


def test_custom_priority_does_not_affect_metadata_ranked_tiers():
    """reasoning/low_cost rank on metadata, so provider preference is irrelevant."""
    router = build_router(priority=[ANTHROPIC, OPENAI])

    assert [c.model for c in router.candidates_for("reasoning")] == [
        "o3",
        "claude-opus-4-8",
    ]
    assert [c.model for c in router.candidates_for("low_cost")] == [
        "gpt-4o-mini",
        "claude-haiku-4-5",
    ]


def test_strategies_override_replaces_the_per_tier_map():
    """Passing `strategies` drops the built-in reasoning/low_cost mapping, tiers
    left out of the override fall back to the default strategy.
    """
    registry = {
        "fast": ModelMetadata(OPENAI, 0.01, 0.02, reasoning_score=10, latency_score=99),
        "slow": ModelMetadata(ANTHROPIC, 0.001, 0.002, reasoning_score=99, latency_score=1),
    }
    catalog = {"reasoning": [Candidate(ANTHROPIC, "slow"), Candidate(OPENAI, "fast")]}

    router = build_router(
        catalog=catalog,
        registry=registry,
        strategies={"reasoning": LatencyStrategy(registry)},
    )

    # ranked by latency, not by reasoning score: the built-in map was replaced.
    assert [c.model for c in router.candidates_for("reasoning")] == ["fast", "slow"]


def test_build_router_validates_the_catalog():
    """Construction fails fast rather than deferring the error to a live request."""
    catalog = {"default": [Candidate(OPENAI, "not-registered")]}

    with pytest.raises(ValueError):
        build_router(catalog=catalog)


def test_default_catalog_and_registry_agree():
    """Guards the shipped configuration itself: adding a catalog entry without a
    registry entry should break here, not in production.
    """
    validate_registry(DEFAULT_CATALOG, MODEL_REGISTRY)


# validate_registry

def test_validate_registry_rejects_model_missing_from_registry():
    catalog = {"default": [Candidate(OPENAI, "ghost-model")]}

    with pytest.raises(ValueError, match="ghost-model"):
        validate_registry(catalog, MODEL_REGISTRY)


def test_validate_registry_rejects_provider_mismatch():
    """gpt-4o is registered under OpenAI, listing it under Anthropic would route
    the request to a provider that cannot serve it.
    """
    catalog = {"default": [Candidate(ANTHROPIC, "gpt-4o")]}

    with pytest.raises(ValueError, match="gpt-4o"):
        validate_registry(catalog, MODEL_REGISTRY)


def test_validate_registry_accepts_an_empty_catalog():
    validate_registry({}, MODEL_REGISTRY)


# Strategy ranking in isolation


def test_priority_strategy_puts_unlisted_providers_last():
    """A provider absent from the preference list sorts behind every listed one
    instead of raising.
    """
    strategy = PriorityStrategy([ANTHROPIC])
    candidates = [Candidate(OPENAI, "gpt-4o"), Candidate(ANTHROPIC, "claude-sonnet-5")]

    assert [c.provider for c in strategy.rank(candidates)] == [ANTHROPIC, OPENAI]


def test_metadata_strategy_puts_unregistered_models_last():
    """Defensive: `validate_registry` should make this unreachable via
    build_router, but ranking must not crash on a model it cannot score.
    """
    registry = {"known": ModelMetadata(OPENAI, 0.001, 0.001, reasoning_score=50, latency_score=50)}
    candidates = [Candidate(ANTHROPIC, "unknown"), Candidate(OPENAI, "known")]

    # highest-first strategy: unknown scores -inf
    assert [c.model for c in ReasoningStrategy(registry).rank(candidates)] == [
        "known",
        "unknown",
    ]
    # lowest-first strategy: unknown scores +inf
    assert [c.model for c in CostStrategy(registry).rank(candidates)] == [
        "known",
        "unknown",
    ]


def test_cost_strategy_sums_input_and_output_cost():
    """Ranking on the total, not on input price alone: `cheap_in` has the lower
    input cost but the higher combined cost.
    """
    registry = {
        "cheap_in": ModelMetadata(OPENAI, 0.001, 0.100, reasoning_score=50, latency_score=50),
        "cheap_total": ModelMetadata(ANTHROPIC, 0.002, 0.003, reasoning_score=50, latency_score=50),
    }
    candidates = [Candidate(OPENAI, "cheap_in"), Candidate(ANTHROPIC, "cheap_total")]

    ranked = CostStrategy(registry).rank(candidates)

    assert [c.model for c in ranked] == ["cheap_total", "cheap_in"]


def test_ranking_is_stable_on_ties():
    """Equal metrics keep catalog order, so configuration stays the tiebreaker."""
    registry = {
        "a": ModelMetadata(OPENAI, 0.001, 0.001, reasoning_score=50, latency_score=50),
        "b": ModelMetadata(ANTHROPIC, 0.001, 0.001, reasoning_score=50, latency_score=50),
    }
    candidates = [Candidate(ANTHROPIC, "b"), Candidate(OPENAI, "a")]

    assert [c.model for c in CostStrategy(registry).rank(candidates)] == ["b", "a"]
    assert [c.model for c in ReasoningStrategy(registry).rank(candidates)] == ["b", "a"]


def test_ranking_does_not_mutate_the_catalog_list():
    """The catalog is process-wide and shared; ranking must return a new list."""
    original = [Candidate(ANTHROPIC, "claude-sonnet-5"), Candidate(OPENAI, "gpt-4o")]
    candidates = list(original)

    ranked = PriorityStrategy(DEFAULT_PRIORITY).rank(candidates)

    assert candidates == original
    assert ranked is not candidates


def test_single_candidate_tier_resolves_to_that_candidate():
    registry = {"only": ModelMetadata(OPENAI, 0.001, 0.001, reasoning_score=50, latency_score=50)}
    catalog = {"solo": [Candidate(OPENAI, "only")]}

    router = build_router(catalog=catalog, registry=registry)

    assert router.candidates_for("solo") == [Candidate(OPENAI, "only")]


def test_routing_service_uses_default_strategy_for_unmapped_tiers():
    """RoutingService directly, without build_router's wiring."""
    catalog = {"anything": [Candidate(ANTHROPIC, "claude-sonnet-5"), Candidate(OPENAI, "gpt-4o")]}
    service = RoutingService(
        catalog=catalog,
        strategies={},
        default_strategy=PriorityStrategy(DEFAULT_PRIORITY),
    )

    assert [c.provider for c in service.candidates_for("anything")] == [OPENAI, ANTHROPIC]
