"""FastAPI dependency providers
"""

from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.accounting.pricing import PricingTable
from app.core.accounting.usage_recorder import UsageRecorder
from app.core.adapters.registry import AdapterRegistry
from app.core.ratelimit.base import RateLimiter
from app.core.routing.router import RoutingService
from app.infra.config import settings
from app.infra.database.session import get_session
from app.infra.global_exceptions import Unauthorized
from app.infra.security.crypto import hash_api_key
from app.models.db.api_key import Api_Key
from app.repositories.usage_repo import UsageRepo
from app.services.gateway_service import GatewayService


async def get_registry(request: Request) -> AdapterRegistry:
    """resolve the process-wide adapter registry built during startup"""
    return request.app.state.registry


async def get_pricing_table(request: Request) -> PricingTable:
    """resolve the process-wide pricing table built during startup"""
    return request.app.state.pricing


async def get_router(request: Request) -> RoutingService:
    """resolve the process-wide routing service built during startup"""
    return request.app.state.router


async def get_usage_recorder(
    pricing: PricingTable = Depends(get_pricing_table),
    session: AsyncSession = Depends(get_session),
) -> UsageRecorder:
    """construct a request-scoped UsageRecorder over the current DB session"""
    return UsageRecorder(pricing, UsageRepo(session))


async def get_gateway_service(
    registry: AdapterRegistry = Depends(get_registry),
    router: RoutingService = Depends(get_router),
    recorder: UsageRecorder = Depends(get_usage_recorder),
) -> GatewayService:
    """construct a GatewayService over the shared registry, router and recorder"""
    return GatewayService(registry, router, recorder)


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


async def get_current_api_key_id(
    api_key: Api_Key = Depends(get_current_api_key),
) -> UUID:
    """the authenticated caller's api_key id"""
    return api_key.id


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
