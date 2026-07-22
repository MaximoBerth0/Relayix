"""Redis-backed idempotency store.

Runs on the same Redis the rate limiter uses. The whole dedup guarantee rests on
one atomic primitive: SET key value NX EX ttl. Exactly one concurrent caller wins
the SET, becomes the owner, and runs the request; everyone else reads the record.
"""

from __future__ import annotations

import json
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models.domain.enums import IdempotencyStatus
from app.models.domain.idempotency import ReservationOutcome
from app.services.exceptions import IdempotencyStoreUnavailable

# how many times to re-attempt the claim if the record vanishes (TTL expiry)
# between our SET NX and the follow-up GET
_RESERVE_ATTEMPTS = 3


class RedisIdempotencyStore:
    """Implements the IdempotencyStore port on top of async Redis"""

    def __init__(
        self,
        client: Redis,
        *,
        inflight_ttl_s: int,
        completed_ttl_s: int,
    ) -> None:
        self._client = client
        # lock lifetime while the original request runs.
        self._inflight_ttl_s = inflight_ttl_s
        # how long a completed response stays replayable.
        self._completed_ttl_s = completed_ttl_s

    @staticmethod
    def _key(api_key_id: UUID, key: str) -> str:
        # scope by api key: the same key from two tenants must stay independent.
        return f"idem:{api_key_id}:{key}"

    async def reserve(
        self, api_key_id: UUID, key: str, fingerprint: str
    ) -> ReservationOutcome:
        redis_key = self._key(api_key_id, key)
        record = json.dumps(
            {
                "status": IdempotencyStatus.IN_PROGRESS.value,
                "fingerprint": fingerprint,
                "response": None,
            }
        )

        try:
            for _ in range(_RESERVE_ATTEMPTS):
                acquired = await self._client.set(
                    redis_key, record, nx=True, ex=self._inflight_ttl_s
                )
                if acquired:
                    return ReservationOutcome(acquired=True)

                raw = await self._client.get(redis_key)
                if raw is None:
                    # claimed then expired between SET and GET, retry the claim.
                    continue

                existing = json.loads(raw)
                return ReservationOutcome(
                    acquired=False,
                    status=existing["status"],
                    request_fingerprint=existing["fingerprint"],
                    response_body=existing["response"],
                )
        except (RedisError, OSError) as exc:
            raise IdempotencyStoreUnavailable() from exc

        # every attempt raced to expiry, surface as retryable rather than loop.
        raise IdempotencyStoreUnavailable()

    async def complete(
        self, api_key_id: UUID, key: str, fingerprint: str, response_body: dict
    ) -> None:
        record = json.dumps(
            {
                "status": IdempotencyStatus.COMPLETED.value,
                "fingerprint": fingerprint,
                "response": response_body,
            }
        )
        try:
            # overwrite the in-flight claim and switch to the longer
            # retention TTL so the response stays replayable
            await self._client.set(
                self._key(api_key_id, key), record, ex=self._completed_ttl_s
            )
        except (RedisError, OSError) as exc:
            raise IdempotencyStoreUnavailable() from exc

    async def release(self, api_key_id: UUID, key: str) -> None:
        try:
            await self._client.delete(self._key(api_key_id, key))
        except (RedisError, OSError):
            pass
