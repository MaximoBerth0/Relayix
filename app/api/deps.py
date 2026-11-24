"""FastAPI dependency providers
"""

import secrets
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.accounting.pricing import PricingTable
from app.core.accounting.registry import CounterRegistry
from app.core.accounting.usage_recorder import UsageRecorder
from app.core.adapters.registry import AdapterRegistry
from app.core.ratelimit.base import RateLimiter
from app.core.routing.router import RoutingService
from app.infra.config import settings
from app.infra.database.session import AsyncSessionLocal, get_session
from app.infra.global_exceptions import Unauthorized
from app.infra.idempotency.redis_store import RedisIdempotencyStore
from app.infra.quota.redis_quota import RedisTokenQuota
from app.infra.security.crypto import hash_api_key
from app.infra.usage_queue.redis_queue import RedisUsageQueue
from app.models.db.api_key import Api_Key
from app.repositories.api_key_repo import ApiKeyRepo
from app.repositories.usage_repo import CommittingUsageRepo, UsageRepo
from app.services.api_key_service import ApiKeyService
from app.services.gateway_service import GatewayService
from app.services.idempotency_service import IdempotencyService
from app.services.token_budget_service import TokenBudgetService
from app.services.usage_service import UsageService


async def get_registry(request: Request) -> AdapterRegistry:
    """resolve the process-wide adapter registry built during startup"""
    return request.app.state.registry


async def get_pricing_table(request: Request) -> PricingTable:
    """resolve the process-wide pricing table built during startup"""
    return request.app.state.pricing


async def get_counters(request: Request) -> CounterRegistry:
    """resolve the process-wide token counter registry built during startup"""
    return request.app.state.counters


async def get_router(request: Request) -> RoutingService:
    """resolve the process-wide routing service built during startup"""
    return request.app.state.router


async def get_usage_recorder(
    request: Request,
    pricing: PricingTable = Depends(get_pricing_table),
    session: AsyncSession = Depends(get_session),
) -> UsageRecorder:
    """construct a request-scoped UsageRecorder over the current DB session"""
    return UsageRecorder(pricing, UsageRepo(session), RedisUsageQueue(request.app.state.redis))


async def get_detached_usage_recorder(
    request: Request,
    pricing: PricingTable = Depends(get_pricing_table),
) -> UsageRecorder:
    """a UsageRecorder that commits on its own session, for streams the client abandoned"""
    return UsageRecorder(
        pricing, CommittingUsageRepo(AsyncSessionLocal), RedisUsageQueue(request.app.state.redis)
    )


async def get_idempotency_service(request: Request) -> IdempotencyService:
    """construct an IdempotencyService over the shared Redis client"""
    store = RedisIdempotencyStore(
        request.app.state.redis,
        inflight_ttl_s=settings.idempotency_inflight_ttl_s,
        completed_ttl_s=settings.idempotency_ttl_s,
    )
    return IdempotencyService(store, refresh_every_s=settings.idempotency_inflight_ttl_s / 3)


async def get_current_api_key(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Api_Key:
    """authenticate the caller's bearer token and return its api_key row.
    returns the whole row (not just the id) 
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise Unauthorized()

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise Unauthorized()

    key_hash = hash_api_key(token)
    api_key = await session.scalar(
        select(Api_Key).where(
            Api_Key.key_hash == key_hash,
            Api_Key.is_active.is_(True),
        )
    )
    if api_key is None:
        raise Unauthorized()

    return api_key


async def get_token_budget_service(
    request: Request,
    api_key: Api_Key = Depends(get_current_api_key),
    router: RoutingService = Depends(get_router),
    counters: CounterRegistry = Depends(get_counters),
    session: AsyncSession = Depends(get_session),
) -> TokenBudgetService:
    """the caller's token budget, one instance per request shared by the route and the gateway"""
    return TokenBudgetService(
        api_key_id=api_key.id,
        tokens_per_minute=api_key.rate_limit_tpm or settings.default_rate_limit_tpm,
        monthly_quota=api_key.monthly_token_quota,
        limiter=request.app.state.token_limiter,
        quota=RedisTokenQuota(request.app.state.redis),
        usage=UsageRepo(session),
        router=router,
        counters=counters,
    )


async def get_gateway_service(
    registry: AdapterRegistry = Depends(get_registry),
    router: RoutingService = Depends(get_router),
    recorder: UsageRecorder = Depends(get_usage_recorder),
    detached_recorder: UsageRecorder = Depends(get_detached_usage_recorder),
    counters: CounterRegistry = Depends(get_counters),
    budget: TokenBudgetService = Depends(get_token_budget_service),
) -> GatewayService:
    """construct a GatewayService over the shared registry, router, recorders, counters and budget"""
    return GatewayService(registry, router, recorder, detached_recorder, counters, budget)


async def get_usage_service(
    session: AsyncSession = Depends(get_session),
) -> UsageService:
    """construct a request-scoped UsageService over the current DB session"""
    return UsageService(UsageRepo(session))


async def get_current_api_key_id(
    api_key: Api_Key = Depends(get_current_api_key),
) -> UUID:
    """the authenticated caller's api_key id"""
    return api_key.id


async def get_current_admin(authorization: str | None = Header(default=None)) -> None:
    """authenticate the caller against the admin master token, gating the
    api-key management endpoints."""
    if not authorization or not authorization.startswith("Bearer "):
        raise Unauthorized()

    token = authorization.removeprefix("Bearer ").strip()
    if not token or not secrets.compare_digest(token, settings.admin_api_token):
        raise Unauthorized()


async def get_api_key_service(
    session: AsyncSession = Depends(get_session),
) -> ApiKeyService:
    """construct a request-scoped ApiKeyService over the current DB session"""
    return ApiKeyService(ApiKeyRepo(session))


async def get_rate_limiter(request: Request) -> RateLimiter:
    """resolve the process-wide resilient rate limiter built during startup"""
    return request.app.state.rate_limiter


async def enforce_rate_limit(
    api_key: Api_Key = Depends(get_current_api_key),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:
    """throttle the caller before the request reaches a provider"""
    rpm = api_key.rate_limit_rpm or settings.default_rate_limit_rpm
    await limiter.check(api_key.id, rpm)
