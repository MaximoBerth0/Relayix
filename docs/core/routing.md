# routing

Turns a caller-facing routing key (a capability "tier") into an ordered list of `(provider, model)` candidates to try. Routing decides preference order; it does not call anything.

## Files

| File | What it does |
|------|--------------|
| `strategies.py` | The `Candidate` and `ModelMetadata` types, the `RoutingStrategy` contract, and its implementations. |
| `router.py` | `RoutingService`, the default catalog, the model registry, validation, and the builder. |

## Tiers and the catalog

Callers ask for a tier, not a concrete model. The catalog maps each tier to the candidates that can serve it:

```python
DEFAULT_CATALOG = {
    "default":   [OPENAI/gpt-4o,       ANTHROPIC/claude-sonnet-5],
    "reasoning": [OPENAI/o3,           ANTHROPIC/claude-opus-4-8],
    "low_cost":  [OPENAI/gpt-4o-mini,  ANTHROPIC/claude-haiku-4-5],
}
```

This is what makes failover possible: a tier has more than one candidate, so if the first provider's circuit is open, the request pipeline can fall through to the next.

## Candidate

```python
@dataclass(frozen=True)
class Candidate:
    provider: ProviderEnum
    model: str
```

Frozen and hashable, one provider plus the exact model string to send upstream.

## Model registry

`MODEL_REGISTRY` is the single source of truth for the per-model facts strategies rank on. Keyed by model string, so adding a model — or a whole provider — is a one-line change that every metric strategy picks up automatically.

```python
@dataclass(frozen=True)
class ModelMetadata:
    provider: ProviderEnum
    input_cost: float      # USD per 1K input tokens
    output_cost: float     # USD per 1K output tokens
    reasoning_score: int   # higher = smarter   (0-100 heuristic)
    latency_score: int     # higher = faster    (0-100 heuristic)
```

Costs are concrete USD-per-1K-token figures; the scores are relative heuristics used only to compare models against each other. (OpenAI cost figures are approximate placeholders — verify before relying on them.)

## Strategies

A `RoutingStrategy` has one method, `rank(candidates) -> candidates`, returning them reordered best-first. Ranking is separate from the catalog so you can change preference order without touching the tier map.

- **`PriorityStrategy`**: orders candidates by a fixed provider ranking (lower index = higher priority; unranked providers sort last). Provider-level, so it does not read the registry.
- **`MetadataStrategy`** (abstract base): ranks by a single field of a candidate's `ModelMetadata`. Subclasses pick the field (`_metric`) and direction (`_higher_is_better`); a candidate whose model is missing from the registry is ranked worst, so it's only tried as a last resort.
  - **`CostStrategy`** — cheapest first, by `input_cost + output_cost`.
  - **`ReasoningStrategy`** — highest `reasoning_score` first.
  - **`LatencyStrategy`** — highest `latency_score` (fastest) first.

## RoutingService

```python
service.candidates_for(tier) -> list[Candidate]  # best first
```

Each tier is ranked by **its own** strategy. The service holds a `strategies` map (tier → strategy) plus a `default_strategy` fallback; `candidates_for` looks the tier up in the catalog, selects the tier's strategy (or the fallback), and ranks. An unknown tier raises `ProviderNotAvailable`.

The default wiring:

| Tier | Strategy | Ranks by |
|------|----------|----------|
| `reasoning` | `ReasoningStrategy` | reasoning score, highest first |
| `low_cost` | `CostStrategy` | total token cost, cheapest first |
| `default` (and any unmapped tier) | `PriorityStrategy` | provider preference (OpenAI, then Anthropic) |

The tier → strategy map keys on tier names, so a strategy is applied only when its name matches a catalog key. A tier with no explicit entry rides the `default_strategy`.

## Validation

`validate_registry(catalog, registry)` runs at router construction and fails fast if the two disagree: every model a tier can route to must have a registry entry, and that entry's provider must match the catalog's. A misconfigured catalog raises `ValueError` here instead of erroring at request time.

## Builder

```python
build_router(catalog=None, priority=None, registry=None, strategies=None) -> RoutingService
```

Builds the process-wide service, defaulting to `DEFAULT_CATALOG`, `DEFAULT_PRIORITY` (OpenAI, then Anthropic), `MODEL_REGISTRY`, and the per-tier strategy map above. It validates the catalog against the registry before returning. Pass `strategies` to override the tier → strategy map.
