"""Redis connection factory"""

from redis.asyncio import Redis

from app.infra.config import settings


def build_redis_client() -> Redis:
    """construct the shared, pooled async Redis client (built once at startup)"""
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.redis_connect_timeout_s,
        socket_timeout=settings.redis_command_timeout_s,
        max_connections=settings.redis_max_connections,
        decode_responses=False,
    )
