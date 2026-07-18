# adapters

Per-provider clients hidden behind one interface. Everything above this module talks to providers through `ProviderAdapter` and never imports the OpenAI or Anthropic SDKs directly.

## Files

| File | What it does |
|------|--------------|
| `base.py` | The `ProviderAdapter` contract. |
| `openai_adapter.py` | Talks to OpenAI chat completions, normalizes the result. |
| `anthropic_adapter.py` | Talks to Anthropic messages, normalizes the result. |
| `registry.py` | Holds one adapter per provider and wires in resilience. |

## The contract

```python
class ProviderAdapter(ABC):
    async def complete(self, request: ChatRequest) -> ChatResponse: ...
```

One method. A concrete adapter takes the normalized `ChatRequest`, translates it into the provider's own API shape, calls the provider, and translates the raw response back into a `ChatResponse`. Callers get the same request and response types regardless of which provider served them.

## Normalization

Each adapter maps the provider's finish/stop vocabulary into one shared set (`stop`, `length`, `tool_use`, `content_filter`), so downstream code never branches on provider-specific strings. Anthropic requires `max_tokens`, so both adapters fall back to `_DEFAULT_MAX_TOKENS = 4096` when the request leaves it unset.

The Anthropic adapter also splits `system` messages out of the message list, because Anthropic takes the system prompt as a separate `system` argument rather than a message role.

## Errors

Any SDK-level failure (`OpenAIError`, `AnthropicError`) is caught and re-raised as `UpstreamError`. Nothing below this line leaks a provider SDK exception upward, which keeps the resilience layer able to reason about failures with one exception type.

## Registry and wiring

`AdapterRegistry` is a plain `dict[ProviderEnum, ProviderAdapter]` with `register` / `get`. A missing provider raises `AdapterNotRegistered`.

`build_registry(config)` constructs it from configured credentials:

- A provider is registered only when its API key is set. A deployment can run with just one provider.
- Every adapter is wrapped by `_with_resilience`, which gives it its own `CircuitBreaker` and a hard timeout before it goes into the registry. So `registry.get(provider)` always returns a `ResilientAdapter`, never a bare SDK client.

See [resilience](resilience.md) for what that wrapper adds.
