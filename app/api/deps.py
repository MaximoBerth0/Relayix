"""FastAPI dependency providers
"""

from fastapi import Depends, Request

from app.core.adapters.registry import AdapterRegistry
from app.services.gateway_service import GatewayService


def get_registry(request: Request) -> AdapterRegistry:
    """resolve the process-wide adapter registry built during startup"""
    return request.app.state.registry


def get_gateway_service(
    registry: AdapterRegistry = Depends(get_registry),
) -> GatewayService:
    """construct a GatewayService over the shared registry"""
    return GatewayService(registry)
