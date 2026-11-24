# ratelimit

Throttles each API key before its request reaches a provider. The design goal is to stay available: if the shared Redis limiter is unreachable, the gateway falls back to a local limiter rather than failing requests.

The same machinery runs two buckets per key:

| Bucket | Limit | Cost of one request | Redis key |
|---|---|---|---|
| Requests per minute | `rate_limit_rpm`, else `DEFAULT_RATE_LIMIT_RPM` | 1 | `ratelimit:{key}` |
| Tokens per minute | `rate_limit_tpm`, else `DEFAULT_RATE_LIMIT_TPM` | estimated prompt tokens, corrected later | `ratelimit:tpm:{key}` |

The monthly token quota is not a bucket, it lives in the token budget service, see [Token budget](#token-budget).

## Files

| File | What it does |
|------|--------------|
| `base.py` | The `RateLimiter` protocol. |
| `token_bucket.py` | In-memory token-bucket limiter (the fallback). |
| `resilient.py` | Composes a primary limiter with a fallback behind a breaker. |

The Redis limiter lives in `app/infra/ratelimit/redis_limiter.py`, the token budget in `app/services/token_budget_service.py`.

## The contract

```python
class RateLimiter(Protocol):
    async def check(self, key: UUID, per_minute: int, cost: float = 1.0) -> None: ...
    async def charge(self, key: UUID, per_minute: int, cost: float) -> None: ...
```

- `check` is pass-or-raise: it spends `cost` and returns `None`, or raises `RateLimitExceeded` when the bucket can't cover it.
- `charge` always applies and never raises `RateLimitExceeded`: a positive cost spends (the bucket may go negative), a negative one refunds (never above capacity). It corrects a cost that was only estimated at `check` time.

`per_minute` is the caller's own limit, resolved from the API key at the call site. Everything in the request path depends on this protocol, never on a concrete backend, which is what lets the Redis limiter (in `infra`) and the in-memory limiter (here in `core`) be swapped or composed freely.

## Bucket rules

Shared by the Redis Lua script and the in-memory limiter:

- the bucket holds up to `per_minute` tokens (the burst allowance) and refills continuously at `per_minute / 60` per second, capped at capacity so idle time cannot bank unlimited burst;
- a brand-new caller starts with a full bucket;
- a rejected `check` carries `retry_after_s`, the exact wait until the bucket covers the cost;
- a cost larger than the whole bucket passes only when the bucket is full, leaving it negative. Otherwise a prompt bigger than the key's TPM could never get through; this way it passes once and the caller pays the debt by waiting.

## InMemoryRateLimiter

Pure token-bucket logic, no Redis, no HTTP, no DB, so it lives in core and is trivially unit-testable. State is per process and guarded by a single `asyncio.Lock`. Under N workers each keeps its own buckets, so while this limiter is in charge the effective ceiling is roughly `N * per_minute`. That is the deliberate trade for a Redis outage: stay up, loosen the limit.

## ResilientRateLimiter

Composes a primary (Redis) with a fallback (in-memory), guarded by a `CircuitBreaker` (see [resilience](resilience.md)). `check` and `charge` follow the same rules:

- breaker open: skip Redis, serve from the fallback.
- Redis raises `RateLimiterUnavailable`: record a failure, log, and serve from the fallback.
- Redis raises `RateLimitExceeded`: that is a real answer, so record a success and re-raise.
- Redis allows: record a success and return.

Note the distinction that keeps this correct: a rate-limit rejection counts as Redis being healthy, only a backend outage trips the breaker toward the fallback. Each bucket (requests, tokens) gets its own `ResilientRateLimiter` and breaker, built once in `app/main.py`.

## Token budget

Requests per minute alone don't bound cost: providers charge by token, so 60 requests of 100K tokens each would pass. `TokenBudgetService` adds the tokens-per-minute bucket and the monthly quota (`api_key.monthly_token_quota`). It is request-scoped, one instance shared by the chat route and the gateway.

**`admit(request)`**, called by the route before idempotency and before any provider:

1. estimate the prompt tokens with the [token counter](accounting.md#token-counting) of the tier's first candidate (an unknown tier raises the same `503` the gateway would);
2. if the key has a monthly quota: reject with `429 TOKEN_QUOTA_EXCEEDED` when `used this month + estimate > quota`, with `Retry-After` pointing at the start of next month;
3. `check` the TPM bucket with the estimate as cost, `429 RATE_LIMITED` if it can't cover it.

**`settle(response)`**, called by `GatewayService._bill` on every billing path (complete, stream, interrupted stream, abandoned stream): `charge` the TPM bucket with `real tokens - estimate`, and add the real tokens to the monthly counter. It never raises: the provider already billed, so a Redis failure here is only logged.

The monthly counter is `quota:{api_key_id}:{YYYY-MM}` in Redis (`app/infra/quota/redis_quota.py`). A new month is a new key, so nothing resets it; it expires after 35 days. Postgres stays the source of truth:

- a missing counter (first request of the month, Redis restarted or evicted it) is seeded from `usage_record` with `SET NX`, never assumed to be zero;
- `settle` only increments a counter that exists, so it can't recreate one at 0 and hide the month's earlier usage from the seed.

Trade-offs, all deliberate:

- **Approximate under concurrency.** Parallel requests read the same `used` and can each pass, so a key can overshoot its quota by a few requests. Exact enforcement would need a per-request reservation on the hot path.
- **Redis down skips the monthly check** (logged). It's a budget, not a security boundary. The TPM bucket falls back to the in-memory limiter like the request bucket.
- **Only the prompt is estimated.** Completion tokens are charged at `settle`, so one large answer can still land past the limit; the debt delays the next requests.
- **A request that fails or is an idempotent replay keeps its estimate on the TPM bucket.** It refills within a minute; the monthly counter only ever sees real usage.
