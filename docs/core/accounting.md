# accounting

Answers "how many tokens did this cost, and what is that in money". Token counting runs before a request (the token budget's estimate) and fills in counts a cut stream never received; pricing and recording run after, once the provider reports real usage.

## Files

| File | What it does |
|------|--------------|
| `token_counter.py` | The `TokenCounter` contract. |
| `openai_counter.py` | Exact prompt-token count via tiktoken. |
| `anthropic_counter.py` | Prompt-token estimate via a local heuristic. |
| `registry.py` | Holds one counter per provider. |
| `pricing.py` | Rate history per (provider, model) and cost computation. |
| `usage_recorder.py` | Prices a finished response and persists a usage record, queueing it for retry if the insert fails. |

## Token counting

```python
class TokenCounter(ABC):
    def count(self, request: ChatRequest) -> int: ...
    def count_completion(self, model: str, content: str) -> int: ...
```

`count` returns the prompt tokens a request will consume, `count_completion` the output tokens a piece of content cost. They are used in two places, never for pricing a normal response: the [token budget](ratelimit.md#token-budget) estimates the prompt at admission, and the gateway estimates the counts of a stream that ended before the provider reported usage (the record is flagged `tokens_estimated`). Two implementations:

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

The table is loaded once at startup, and startup refuses to boot if any catalog model has no rate in effect (`validate_pricing`, see [routing](routing.md#validation)). So the gateway never calls a model it can't price, and a lookup at request time can't miss. Migration `0003` seeds a rate for every model in the default catalog, inserting only for pairs that have none yet.

## Usage recording

`UsageRecorder` is the after-the-fact step. Given the api key id, a completed `ChatResponse` and the catalog model the gateway asked for, it:

1. prices the response with the pricing table, by the **catalog model**, using the real token counts the provider returned;
2. builds a validated `UsageRecord`, with its own `id` (uuid7) assigned right away;
3. saves it through the `UsageRepository` port.

Pricing uses the catalog model, not `response.model`, because providers report their own names: OpenAI answers a `gpt-4o` request as `gpt-4o-2024-08-06`. The reported name is kept on the record as `upstream_model`, for audit, and `model` stays the catalog name, so usage by model doesn't split across snapshots.

`UsageRepository` is a Protocol implemented by the repositories layer, so core stays free of any storage detail. `UsageRepo.save` runs the insert inside a savepoint, so a failed insert never poisons the request's transaction, and it is `ON CONFLICT (id) DO NOTHING`, so saving the same record twice inserts it once.

### When recording fails

By the time the recorder runs, the provider has already executed and billed the request. So bookkeeping never fails the caller's response:

- **The insert fails** (db blip, timeout): the record, already priced, is pushed to the `PendingUsageQueue` port, a Redis list, and the response goes out as usual. The retry worker persists it later.
- **Pricing or validation fails**: that would fail the same way on every retry, so it is not queued. `GatewayService._bill` logs it with everything needed to rebuild the record (api key, models, tokens, `request_id`). Startup validation makes the pricing case unreachable in practice.
- **Redis is down too**: same, only the log keeps it.

The idempotency layer follows the same rule: if storing the response under its `Idempotency-Key` fails, the failure is logged and the response is still returned.

## Retry worker

A separate long-lived process, `python -m app.workers.usage_retry` (`make worker`), that drains the pending queue. It shares the image and settings with the API.

```
usage:pending ──BLMOVE──> usage:processing ──insert + commit──> Postgres
                                 │                                 │
                                 │ failed, attempts < max          └─ LREM from processing
                                 ├──> back to usage:pending, wait USAGE_RETRY_BACKOFF_S
                                 │ failed, attempts = max
                                 └──> usage:dead
```

- An entry leaves `processing` only after its insert committed. On startup the worker moves anything left in `processing` (a crash) back to `pending`.
- Every path is at least once, and the insert dedupes on the record id, so a record processed twice is still counted once. Running more than one worker is safe for the same reason.
- An entry that fails `USAGE_RETRY_MAX_ATTEMPTS` times (default 10) moves to `usage:dead` for a human to look at.
- The record keeps the `created_at` and cost it was built with, even if the worker persists it hours later.

| File | What it does |
|------|--------------|
| `app/infra/usage_queue/redis_queue.py` | The three Redis lists and the record encoding. |
| `app/services/usage_retry_service.py` | One claim, persist, ack/retry/bury step. |
| `app/workers/usage_retry.py` | The process: wiring, the loop, backoff, SIGTERM. |
