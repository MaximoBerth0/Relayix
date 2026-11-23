# New Changes

Fixes for gaps #1, #2, #3 and #4 from the known gaps list.

## 1. The usage retry worker now runs on ECS

**Before:** `app/workers/usage_retry.py` only ran locally (`make worker`). On ECS nothing drained `usage:pending`, so failed usage inserts were never persisted in production.

**Now:** `deploy/aws/app.yaml` has a second service on the same image:

- `WorkerTaskDefinition` (`relayix-usage-retry`): command `python -m app.workers.usage_retry`, same `DATABASE_URL`, `REDIS_URL` and `ADMIN_API_TOKEN` secrets as the api, a DB pool of 1 + 1 overflow, its own log group `/ecs/relayix/usage-retry`.
- `WorkerService`: `DesiredCount: 1`, no load balancer, deployment circuit breaker with rollback, created under the same `RunService` condition as the api (so it starts only after the first migration).
- `Cpu: 0` (no CPU reservation): two api tasks already fill the 2048 units of a t4g.medium during a rolling deploy, and the worker spends its time blocked on Redis. It only reserves 192 MB of memory.
- `StopTimeout: 30`: on SIGTERM the loop finishes the entry in hand. An entry cut mid-insert stays in `usage:processing` and is requeued when the next task starts.
- More than one task at a time (for example while a deploy overlaps the old and new task) is safe, because the insert dedupes on the record id.

Also, the worker's inline `persist()` function was replaced by `CommittingUsageRepo` (see #3), which is now shared with the API.

## 2. A long stream keeps its idempotency key

**Before:** the in-flight idempotency record had a fixed 120 s TTL. The provider timeout applies per event, so a stream could run for minutes. When the TTL expired mid-stream, a retry with the same key got the slot and ran the request a second time.

**Now:**

- `RedisIdempotencyStore.refresh()` runs `EXPIRE` on the in-flight key with the in-flight TTL. It is part of the `IdempotencyStore` port.
- `IdempotencyService._settle` refreshes the claim while events flow, once every `refresh_every_s` (wired as a third of `idempotency_inflight_ttl_s`, so 40 s by default).
- A refresh is best effort: a failure is logged and the stream continues. The worst case is the old behaviour (the claim expires at the TTL).
- `IdempotencyService` now takes a required keyword argument, `refresh_every_s`.

## 3. A client disconnect mid-stream records the partial usage

**Before:** when the client went away, the streaming generator was closed or cancelled. `GatewayService._drain` only billed on the terminal event or on `UpstreamStreamInterrupted`, so the tokens the provider had already generated (and billed) were never recorded. Even if it had tried, the insert would have used the request's session, which is never committed on cancellation.

**Now:**

- `_drain` keeps the content it forwarded. If the stream ends without being billed (disconnect, cancellation, any unexpected error), a `finally` records an errored partial (`finish_reason = "error"`) for the candidate's provider and model.
- That insert goes through a **detached recorder**: a `UsageRecorder` over `CommittingUsageRepo`, which opens its own session and commits right away, so it never depends on the request's transaction. A failed insert is still queued to `usage:pending` like any other.
- The insert runs inside `anyio.CancelScope(shield=True)`. On disconnect Starlette cancels the task's scope, so without the shield every await would raise `CancelledError` again and the insert would never run. A test checks this: it fails without the shield.
- `billed` is only set after the insert returns, so a cancellation during the normal billing still falls back to the partial record. A stream that completed is never recorded twice.
- `GatewayService` takes a fourth argument, `detached_recorder`, built in `app/api/deps.py` by `get_detached_usage_recorder`.

**Known limit:** token counts only arrive with the terminal event (OpenAI sends usage in the last chunk), so an abandoned stream is recorded with `tokens_in = tokens_out = 0` and cost 0. The row exists and can be found in reconciliation, but the counts still undercount. Estimating them with the token counters is gap #6.

## 4. The retry queue survives a Redis node replacement

**Before:** ElastiCache ran one node with no persistence. That was fine while every key was a token bucket or an idempotency record with a TTL, but the retry queue (`usage:pending`, `usage:processing`, `usage:dead`) has no TTL and is the only copy of those records. A node replacement or restart lost them.

**Now:**

- The `Redis` replication group has a replica in the other AZ: `NumCacheClusters: 2`, `AutomaticFailoverEnabled: true`, `MultiAZEnabled: true`. When the primary fails or is replaced, the replica is promoted with the data and the primary endpoint points to it. All three properties update in place, so the group is not replaced.
- The Redis client (`app/infra/ratelimit/redis_client.py`) now survives the failover itself. On a dropped connection or a `READONLY` reply (a command that reached the demoted primary), it disconnects, reconnects (which resolves the primary endpoint again, now pointing at the promoted replica) and retries up to 2 times with a short exponential backoff (0.1 s base, 1 s cap). This applies to the API and the worker.
- Timeouts are not retried on purpose: a Redis that hangs would multiply each request's latency (3 x 2 s) before the rate limiter's breaker opens.
- `default.redis7` evicts with `volatile-lru`, which only evicts keys with a TTL, so the queue is never evicted under memory pressure.

**Why not SQS:** SQS is durable and has a dead-letter queue built in, but it means rewriting the queue and the worker, plus new IAM and a new dependency. The replica is an infra-only change. A Postgres outbox doesn't work either: the queue exists for the moments Postgres is what's failing.

**Known limits:**

- Replication is asynchronous. A push acknowledged right before the primary dies can still be lost. Closing that window would need `WAIT 1 <timeout>` after the push, or SQS.
- It doubles the cache node cost (two `cache.t4g.micro` nodes).
- Losing both nodes at once still loses the queue.

## Files

- `deploy/aws/app.yaml`: worker log group, task definition and service; Redis replica with automatic failover and Multi-AZ.
- `app/infra/ratelimit/redis_client.py`: reconnect and retry on connection errors and `READONLY`.
- `app/workers/usage_retry.py`: uses `CommittingUsageRepo` instead of its own `persist()`.
- `app/repositories/usage_repo.py`: new `CommittingUsageRepo`.
- `app/api/deps.py`: `get_detached_usage_recorder`, `refresh_every_s` wiring.
- `app/services/gateway_service.py`: partial billing for abandoned streams.
- `app/services/idempotency_service.py`, `app/infra/idempotency/redis_store.py`: in-flight TTL refresh.

## Tests

New tests:

- `test_a_stream_the_client_abandons_still_records_its_partial_usage`
- `test_partial_usage_is_recorded_even_while_the_task_is_cancelled`
- `test_a_completed_stream_is_not_recorded_again_on_close`
- `test_a_long_stream_keeps_its_in_flight_claim`

The full suite passes: 199 tests against the test Postgres and Redis.

The Redis retry was checked by hand against the test Redis, by making the first command fail: `READONLY` and connection errors reconnect and succeed on the second attempt, a timeout fails right away. There is no automated test for it.

Not verified: the CloudFormation template was only checked to parse (cfn-lint is not installed). It has not been deployed, and no real failover has been exercised.
