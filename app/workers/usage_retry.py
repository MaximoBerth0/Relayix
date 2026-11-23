"""Usage retry worker: `python -m app.workers.usage_retry`.

A long-lived process next to the API. It persists the usage records the
request path couldn't insert, so a provider call that was billed is never
missing from the ledger for long.
"""

import asyncio
import logging
import signal

from app.infra.config import settings
from app.infra.database.session import AsyncSessionLocal, engine
from app.infra.ratelimit.redis_client import build_redis_client
from app.infra.usage_queue.redis_queue import RedisUsageQueue
from app.observability.logging import setup_logging
from app.repositories.usage_repo import CommittingUsageRepo
from app.services.usage_retry_service import RetryOutcome, UsageRetryService

logger = logging.getLogger(__name__)

# below redis_command_timeout_s, or the blocking read itself times out
_CLAIM_TIMEOUT_S = 1.0


async def run() -> None:
    setup_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    redis_client = build_redis_client()
    queue = RedisUsageQueue(redis_client)
    service = UsageRetryService(
        queue, CommittingUsageRepo(AsyncSessionLocal).save, settings.usage_retry_max_attempts
    )

    requeued = await queue.requeue_processing()
    logger.info("usage retry worker started", extra={"requeued": requeued})

    try:
        while not stop.is_set():
            try:
                outcome = await service.process_next(_CLAIM_TIMEOUT_S)
            except Exception:
                # Redis itself is failing: wait and try again
                logger.exception("usage retry worker could not reach the queue")
                outcome = RetryOutcome.RETRY
            if outcome is RetryOutcome.RETRY:
                # the db (or redis) is likely down, don't spin on it
                try:
                    await asyncio.wait_for(stop.wait(), settings.usage_retry_backoff_s)
                except TimeoutError:
                    pass
    finally:
        await redis_client.aclose()
        await engine.dispose()
        logger.info("usage retry worker stopped")


if __name__ == "__main__":
    asyncio.run(run())
