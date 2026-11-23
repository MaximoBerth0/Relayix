"""Redis connection factory"""

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, ReadOnlyError

from app.infra.config import settings


def build_redis_client() -> Redis:
    """construct the shared, pooled async Redis client (built once at startup)

    During an ElastiCache failover a command can hit a dropped connection or the
    demoted primary (READONLY). Those reconnect, which re-resolves the primary
    endpoint to the promoted replica, and retry a couple of times. Timeouts are
    not retried: a hung Redis would multiply the request's latency before the
    rate limiter's breaker opens.
    """
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.redis_connect_timeout_s,
        socket_timeout=settings.redis_command_timeout_s,
        max_connections=settings.redis_max_connections,
        decode_responses=False,
        retry=Retry(ExponentialBackoff(cap=1.0, base=0.1), retries=2),
        retry_on_error=[ConnectionError, ReadOnlyError],
    )
