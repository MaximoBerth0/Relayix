"""Liveness and readiness probes.

The two answer different questions, and conflating them is actively harmful:

  /health        liveness    the process is up and its event loop is turning.
                             Touches no dependency, because a failing liveness
                             probe gets the container *killed and restarted*,
                             and restarting the app never fixes a Postgres blip.

  /health/ready  readiness   every backing service this process needs is
                             reachable. A failure pulls the instance out of the
                             load balancer without restarting it, so it rejoins
                             on its own once the dependency recovers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import text

from app.infra.config import settings
from app.infra.database.session import engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


async def _ping_postgres() -> None:
    """Cheapest round trip that proves the pool can still hand out a live connection."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _ping_redis(redis: Redis) -> None:
    await redis.ping()


async def _probe(name: str, check: Callable[[], Awaitable[None]]) -> tuple[str, str]:
    """Run one dependency check under a deadline, mapping any failure to a status.
    """
    try:
        async with asyncio.timeout(settings.health_check_timeout_s):
            await check()
    except TimeoutError:
        logger.warning("readiness: %s timed out", name)
        return name, "timeout"
    except Exception as exc:  # noqa: BLE001 any failure at all means "not ready"
        logger.warning("readiness: %s failed: %s", name, exc)
        return name, "error"
    return name, "ok"


@router.get("/health")
async def liveness() -> dict[str, str]:
    """The process is alive and able to answer. Deliberately checks nothing else."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request, response: Response) -> dict[str, object]:
    """Report whether Postgres and Redis are both reachable.

    Returns 200 when every dependency answers, 503 otherwise. The per-dependency
    breakdown is in the body so a failing probe says *which* one is down.
    """
    redis: Redis = request.app.state.redis

    # run concurrently: total latency is the slowest check, not the sum
    checks = dict(
        await asyncio.gather(
            _probe("postgres", _ping_postgres),
            _probe("redis", lambda: _ping_redis(redis)),
        )
    )

    ready = all(state == "ok" for state in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ready" if ready else "not_ready", "checks": checks}
