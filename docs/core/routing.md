# routing

Turns a caller-facing routing key (a capability "tier") into an ordered list of `(provider, model)` candidates to try. Routing decides preference order; it does not call anything.

## Files

| File | What it does |
|------|--------------|
| `strategies.py` | The `Candidate` type, the `RoutingStrategy` contract, and its implementations. |
| `router.py` | `RoutingService`, the default catalog, and the builder. |

## Tiers and the catalog

Callers ask for a tier, not a concrete model. The catalog maps each tier to the candidates that can serve it:

```python
DEFAULT_CATALOG = {
    "fast":  [OPENAI/gpt-4o-mini,  ANTHROPIC/claude-3-5-haiku-latest],
    "smart": [OPENAI/gpt-4o,       ANTHROPIC/claude-3-5-sonnet-latest],
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

## Strategies

A `RoutingStrategy` has one method, `rank(candidates) -> candidates`, returning them reordered best-first. Ranking is separate from the catalog so you can change preference order without touching the tier map.

- **`PriorityStrategy`** (in use): orders candidates by a fixed provider ranking. Lower index means higher priority; unranked providers sort last.
- **`CostStrategy`** / **`LatencyStrategy`**: stubs for cheapest-first and fastest-first ranking. Interfaces are defined, `rank` is not yet implemented.

## RoutingService

```python
service.candidates_for(tier) -> list[Candidate]  # best first
```

Looks the tier up in the catalog, then hands the candidates to the strategy to rank. An unknown tier raises `ProviderNotAvailable`.

`build_router(catalog=None, priority=None)` builds the process-wide service with `PriorityStrategy`, defaulting to `DEFAULT_CATALOG` and `DEFAULT_PRIORITY` (OpenAI, then Anthropic).
