# Relayix docs

One page to start from. Relayix is a FastAPI gateway that sits between your app and
LLM providers (OpenAI, Anthropic): it routes requests, fails over when a provider is
unhealthy, enforces per-key rate limits and token budgets, and records token-based cost
per request.

## Where to read more

- **The request path, step by step:** [system_flow.md](system_flow.md): startup
  validation, admission (auth, rate limits, token budget, idempotency), routing & failover, and the
  usage retry worker.
- **Per module, in depth:**
  [routing](core/routing.md) ·
  [adapters](core/adapters.md) ·
  [resilience](core/resilience.md) (circuit breaker) ·
  [ratelimit](core/ratelimit.md) (incl. token budget) ·
  [accounting](core/accounting.md) (tokens, pricing, usage, retry worker)
- **How it runs in production:** [deployment.md](deployment.md): AWS ECS on EC2,
  the CloudFormation stacks and the release flow.

## Layers

```
HTTP        app/api/v1/*      routers + Pydantic schemas, no business logic
Services    app/services/*    orchestrate core modules into the request pipeline
Core        app/core/*        provider-agnostic logic: routing, adapters, resilience,
                               ratelimit, accounting — no HTTP, no DB
Repos       app/repositories/*  persistence for api keys, usage, pricing
Infra       app/infra/*       config, DB session, Redis client, security, idempotency,
                               monthly token counter, usage retry queue
Workers     app/workers/*     long-lived processes next to the API (usage retry)
```

`app/main.py` wires it all together at startup (adapter registry, router, pricing
table, resilient rate limiters for requests and tokens) and holds them on `app.state` for the lifetime of the
process. Startup fails if any catalog model has no pricing rate in effect.

## Processes

| Process | Command | What it does |
|---|---|---|
| API | `uvicorn app.main:app` (`make run`) | Serves every endpoint below |
| Usage retry worker | `python -m app.workers.usage_retry` (`make worker`) | Persists usage records whose insert failed in the request path, see [accounting](core/accounting.md#retry-worker) |

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /v1/chat/completions` | Send a chat request; routes to a provider, supports SSE streaming (`stream: true`) and an `Idempotency-Key` header |
| `GET /v1/usage` | Aggregate token/cost totals for the caller's api key |
| `GET /v1/usage/records` | Paginated raw usage records for the caller's api key |
| `GET /v1/usage/by-model` | Usage totals grouped by provider/model |
| `POST /v1/api-keys` | Create an api key (admin bearer token required) |
| `GET /v1/api-keys` | List api keys (admin) |
| `POST /v1/api-keys/{id}/revoke` | Deactivate an api key (admin) |
| `GET /health` | Liveness: process is up |
| `GET /health/ready` | Readiness: Postgres and Redis are both reachable |

Caller endpoints (`chat`, `usage`) authenticate with `Authorization: Bearer <api key>`.
Admin endpoints (`api-keys`) authenticate against `ADMIN_API_TOKEN` instead.

## Configuration

Every setting lives in `app/infra/config.py` and is documented there inline;
`.env.example` lists the ones you actually need to set for local dev. Nothing else in
the codebase reads `os.environ` directly.

## Rules the code never bends

- A provider is only registered when its API key is configured — the gateway runs
  fine with just one provider.
- A completed idempotency key never re-runs the request; a different request body
  under the same key is a conflict (422), not a new attempt.
- A provider *timeout* is ambiguous (it may have already billed), so failing over on
  timeout is gated by `failover_policy`, which defaults to `at_most_once` — no
  double-spend. A provider that was never reached (unavailable, circuit open) always
  fails over safely.
- Usage is priced from the provider's real reported token counts, never the
  pre-flight estimate, and by the catalog model, never the name the provider reports.
  The token budget admits on the estimate but is corrected with the real counts.
- A model with no pricing rate is never called: startup refuses to boot.
- Once the provider has answered, bookkeeping never fails the response: a failed
  usage insert is queued for the retry worker, and the idempotency key is never
  released for a request that ran.
- Each provider has its own circuit breaker; one sick provider never blocks the
  others.
