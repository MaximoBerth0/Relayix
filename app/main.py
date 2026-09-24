import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1 import api_keys, chat, health, usage
from app.core.accounting.pricing import build_pricing_table
from app.core.adapters.registry import build_registry
from app.core.exceptions import RateLimitExceeded
from app.core.ratelimit.resilient import ResilientRateLimiter
from app.core.ratelimit.token_bucket import InMemoryRateLimiter
from app.core.resilience.circuit_breaker import CircuitBreaker
from app.core.routing.router import build_router
from app.infra.config import settings
from app.infra.database.session import AsyncSessionLocal
from app.infra.global_exceptions import AppError
from app.infra.ratelimit.redis_client import build_redis_client
from app.infra.ratelimit.redis_limiter import RedisRateLimiter
from app.observability.logging import setup_logging
from app.observability.tracing import setup_tracing
from app.repositories.pricing_repo import load_pricing_rates

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build shared, long-lived resources once per process"""
    setup_logging()  # configure logging before anything emits
    app.state.registry = build_registry()
    app.state.router = build_router()

    # load the pricing rates once and hold the table for the recorder to price against
    async with AsyncSessionLocal() as session:
        rates = await load_pricing_rates(session)
    app.state.pricing = build_pricing_table(rates)

    # rate limiting: Redis is the shared source of truth, the in-memory bucket is
    # the fallback, and a dedicated circuit breaker decides which one is in charge
    redis_client = build_redis_client()
    app.state.redis = redis_client
    app.state.rate_limiter = ResilientRateLimiter(
        primary=RedisRateLimiter(redis_client),
        fallback=InMemoryRateLimiter(),
        breaker=CircuitBreaker(
            fail_threshold=settings.ratelimit_breaker_fail_threshold,
            reset_timeout_s=settings.ratelimit_breaker_reset_timeout_s,
        ),
    )

    try:
        yield
    finally:
        await redis_client.aclose()
        if tracer_provider is not None:
            tracer_provider.shutdown()  # flush pending spans


app = FastAPI(title="Relayix", lifespan=lifespan)

tracer_provider = setup_tracing(app)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(usage.router)
app.include_router(api_keys.router)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Render any AppError as the normalized JSON error body."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RateLimitExceeded)
async def handle_rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Render a 429 with a Retry-After header so clients back off correctly."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
        headers={"Retry-After": str(math.ceil(exc.retry_after_s))},
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Catch anything that isn't an AppError so the API never leaks a bare,
    non-JSON traceback response. The real exception is still logged with its
    stack trace, only the client-facing body is generic.
    """
    logger.exception("unhandled exception", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content={"error_code": "INTERNAL_ERROR", "message": "Internal server error"},
    )
