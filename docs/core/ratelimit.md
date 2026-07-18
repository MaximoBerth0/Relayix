# ratelimit

Throttles requests per API key before they reach a provider. The design goal is to stay available: if the shared Redis limiter is unreachable, the gateway falls back to a local limiter rather than failing requests.

## Files

| File | What it does |
|------|--------------|
| `base.py` | The `RateLimiter` protocol. |
| `token_bucket.py` | In-memory token-bucket limiter (the fallback). |
| `resilient.py` | Composes a primary limiter with a fallback behind a breaker. |

## The contract

```python
class RateLimiter(Protocol):
    async def check(self, key: UUID, rpm: int) -> None: ...
```

One method, pass-or-raise. It returns `None` when the request is allowed and raises `RateLimitExceeded` when it is not. `rpm` is the caller's own limit, resolved from the API key at the call site. Everything in the request path depends on this protocol, never on a concrete backend, which is what lets the Redis limiter (in `infra`) and the in-memory limiter (here in `core`) be swapped or composed freely.

## InMemoryRateLimiter

Pure token-bucket logic, no Redis, no HTTP, no DB, so it lives in core and is trivially unit-testable. Per key:

- the bucket holds up to `rpm` tokens (the burst allowance);
- it refills continuously at `rpm / 60` tokens per second, capped at capacity so idle time cannot bank unlimited burst;
- each request costs one token; no token means denial, and the raised `RateLimitExceeded` carries `retry_after_s`, the exact wait until one token is available.

A brand-new caller starts with a full bucket. State is per process and guarded by a single `asyncio.Lock`. Under N workers each keeps its own buckets, so while this limiter is in charge the effective ceiling is roughly `N * rpm`. That is the deliberate trade for a Redis outage: stay up, loosen the limit.

## ResilientRateLimiter

Composes a primary (Redis) with a fallback (in-memory), guarded by a `CircuitBreaker` (see [resilience](resilience.md)):

- breaker open: skip Redis, serve from the fallback.
- Redis raises `RateLimiterUnavailable`: record a failure, log, and serve from the fallback.
- Redis raises `RateLimitExceeded`: that is a real answer, so record a success and re-raise.
- Redis allows: record a success and return.

Note the distinction that keeps this correct: a rate-limit rejection counts as Redis being healthy, only a backend outage trips the breaker toward the fallback.
