# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Relayix is a FastAPI gateway that sits in front of LLM providers (OpenAI, Anthropic). Application code calls Relayix instead of a provider directly; Relayix routes the request, fails over to another provider when one is unhealthy, enforces per-key rate limits, and records token-based cost per request.

Architecture, the request flow, and per-module docs live under `docs/` (start at `docs/README.md`), read them for detail beyond what's here. Do not edit anything under `docs/` or `README.md` unless explicitly asked.

## Tech stack

FastAPI, SQLAlchemy 2.0 (async, asyncpg), PostgreSQL, Alembic, Pydantic v2 / pydantic-settings, Redis, OpenAI SDK, Anthropic SDK, tiktoken, pytest, Poetry, Docker.

## Commands

```bash
# local stack: Postgres + Redis + api, migrations applied automatically
make up
make down          # stop (keeps volumes)
make logs
make sh             # shell inside the api container

# migrations (run inside the api container)
make migrate
make revision m="describe your change"
make current
make heads

# tests (separate stack, its own ports)
make test-up
make test
make test-down

# lint (run on the host, inside the poetry env)
poetry run ruff check .
poetry run ruff format .
```

`DATABASE_URL` and `ADMIN_API_TOKEN` are required, no defaults. See `.env.example` / `app/infra/config.py` for the full settings list — nothing else in the codebase reads `os.environ` directly. A provider (OpenAI/Anthropic) is only registered when its API key is set, so the gateway runs fine with just one configured.

## Architecture

Layered, not a vertical slice per feature, there is one request pipeline, not independent domains:

```
HTTP        app/api/v1/*        routers + Pydantic schemas, no business logic
Services    app/services/*      orchestrate core modules into the request pipeline
Core        app/core/*          provider-agnostic logic, no HTTP, no DB
Repos       app/repositories/*  persistence for api keys, usage, pricing
Infra       app/infra/*         config, DB session, Redis client, security, idempotency
```

`app/core/` holds the provider-agnostic pieces, each documented in `docs/core/`: `adapters` (one interface over OpenAI/Anthropic SDKs), `routing` (tier → ranked candidates), `resilience` (per-provider circuit breaker), `ratelimit` (token bucket, Redis-backed with an in-memory fallback), `accounting` (token counting, pricing, usage recording).

`app/main.py` builds the process-wide adapter registry, router, pricing table, and resilient rate limiter once at startup and hangs them on `app.state`; `app/api/deps.py` resolves them per request via `Depends`.

Request path — see `docs/system_flow.md` for the full diagrams:
1. **Admission**: authenticate the API key, rate limit it, and — if an `Idempotency-Key` header was sent reserve or replay before any provider is touched.
2. **Routing & failover**: resolve the tier to ranked `(provider, model)` candidates, skip unconfigured or circuit-open ones, call the first that's left under a hard timeout.
3. Success is priced from the provider's real reported token counts (never the pre-flight estimate) and recorded as a `UsageRecord`.

### Rules the code never bends

- A provider is only registered when its API key is configured.
- An unavailable provider (never reached, or circuit open) always fails over safely. A *timeout* is ambiguous the request may have already executed and billed so failing over on timeout is gated by `failover_policy`, defaulting to `at_most_once` (no double-spend).
- A completed idempotency key never re-runs the request; the same key with a different request body is a conflict (422), not a retry.
- Money (usage cost) is computed from the provider's actual returned token counts, not the estimate used for pre-flight checks.
- Each provider has its own circuit breaker; one sick provider never blocks another.

## Testing

`tests/flow/` are integration tests over the real HTTP surface (ASGI transport) against the test Postgres/Redis stack (`make test-up`); `tests/unit/` covers pure logic (routing strategies, circuit breaker states) with no I/O. Provider calls are faked via `tests/fixtures/adapters.py`, never real network calls.

## Conventions

- Routers hold no business logic that lives in `app/services/`.
- `app/core/*` never imports HTTP (FastAPI) or DB (SQLAlchemy) — it stays provider-agnostic and unit-testable in isolation.
- Talk to providers only through `ProviderAdapter`; never import the OpenAI/Anthropic SDKs outside `app/core/adapters/`.
- Money is `Decimal`, never `float`.
- Don't introduce new architectural patterns or abstractions unless something is reused twice.
- Keep comments sparse, code and naming should carry the meaning.
