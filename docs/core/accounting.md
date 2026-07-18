# accounting

Answers "how many tokens did this cost, and what is that in money". Token counting runs before a request (for pre-flight checks and estimates); pricing and recording run after, once the provider reports real usage.

## Files

| File | What it does |
|------|--------------|
| `token_counter.py` | The `TokenCounter` contract. |
| `openai_counter.py` | Exact prompt-token count via tiktoken. |
| `anthropic_counter.py` | Prompt-token estimate via a local heuristic. |
| `registry.py` | Holds one counter per provider. |
| `pricing.py` | Rate history per (provider, model) and cost computation. |
| `usage_recorder.py` | Prices a finished response and persists a usage record. |

## Token counting

```python
class TokenCounter(ABC):
    def count(self, request: ChatRequest) -> int: ...
```

Returns the prompt tokens a request will consume. Two implementations:

- **`OpenAICounter`**: uses `tiktoken`, picking the model's encoding and falling back to `o200k_base` for unknown models. Exact.
- **`AnthropicCounter`**: Anthropic ships no local tokenizer, so this estimates at `3.5` chars per token. An estimate, not a billing source of truth.

Both add a small fixed overhead per message and per reply, matching how the providers frame chat requests.

`CounterRegistry` maps `ProviderEnum` to a counter with `register` / `get`; a missing provider raises `CounterNotRegistered`. `build_registry()` returns one wired for both providers.

## Pricing

`PricingTable` keeps a history of `PricingRate` rows per `(provider, model)` pair, so prices can change over time without losing old ones.

- `register(rate)`: append a rate to its pair's history.
- `get(provider, model, as_of=now)`: return the rate in effect at `as_of`, that is the most recent one whose `effective_from` is not in the future. No effective rate raises `PricingRateNotFound`.
- `cost_for(provider, model, tokens_in, tokens_out, as_of=now)`: resolve the rate and compute the `Decimal` cost.

Cost is `Decimal`, not float, because this is money. `build_pricing_table(rates)` loads a table from rates read out of the pricing repository.

## Usage recording

`UsageRecorder` is the after-the-fact step. Given the api key id and a completed `ChatResponse`, it:

1. prices the response with the pricing table (using the real token counts the provider returned);
2. builds a validated `UsageRecord`;
3. saves it through the `UsageRepository` port.

`UsageRepository` is a Protocol implemented by the repositories layer, so core stays free of any storage detail. The recorder returns the saved record so the caller can surface cost back on the response.
