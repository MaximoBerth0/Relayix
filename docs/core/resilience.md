# resilience

Keeps one sick provider from taking requests down with it. A circuit breaker tracks each provider's recent outcomes and stops sending calls to one that keeps failing, giving it time to recover.

## Files

| File | What it does |
|------|--------------|
| `circuit_breaker.py` | The breaker state machine. |
| `resilient_adapter.py` | Wraps an adapter with a timeout plus a breaker. |

## Circuit breaker

Provider-agnostic. It only counts outcomes and decides whether the next call is allowed. Three states:

```
CLOSED    -> calls pass. Trips to OPEN after `fail_threshold` consecutive failures.
OPEN      -> calls rejected until `reset_timeout_s` elapses, then moves to HALF_OPEN.
HALF_OPEN -> one trial call allowed. Success closes the circuit, failure re-opens it.
```

The API is four members:

- `allow() -> bool`: may the next call go through?
- `record_success()`: resets the failure count and closes.
- `record_failure()`: increments failures, and opens if the threshold is hit or the trial failed.
- `state`: the current `CircuitState`.

All mutating paths are guarded by an `asyncio.Lock`, so concurrent requests to the same provider stay consistent. In `HALF_OPEN` exactly one trial is admitted while it is outstanding; every other caller is rejected until the trial reports back. Timing uses `time.monotonic()`, so it is immune to wall-clock changes.

## ResilientAdapter

A `ProviderAdapter` that wraps another adapter without changing the contract its callers rely on. Per call it:

1. asks the breaker `allow()`; if not, raises `CircuitOpen` immediately (no upstream call).
2. runs the inner `complete` under `asyncio.timeout(timeout_s)`.
3. on timeout: records a failure and raises `UpstreamError`.
4. on `UpstreamError` from the inner adapter: records a failure and re-raises.
5. on success: records a success and returns the response.

This is the layer `build_registry` wraps every adapter in (see [adapters](adapters.md)), so a slow or failing provider is bounded in time and pulled out of rotation automatically. The same `CircuitBreaker` is also reused by the rate limiter to guard its Redis backend (see [ratelimit](ratelimit.md)).
