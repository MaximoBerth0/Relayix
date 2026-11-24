## System flow

The request path is split into two stages: **admission** (is this caller allowed, and
have we already answered this exact request?) and **routing & failover** (which provider
serves it, and what happens when one fails). A third piece runs outside the request: the
**usage retry worker**, which persists usage records whose insert failed.

### High-level flow

```mermaid
flowchart LR
    A[Main backend] -- "POST /v1/chat/completions" --> B[Relayix Gateway]
    B -- response --> A
    B -- request --> G[AI Provider]
    G -- response --> B
    B <--> R[("Redis<br/>rate limits + token quota<br/>+ idempotency + usage retry queue")]
    B --> D[("Postgres<br/>api_keys + usage_records")]
    W[Usage retry worker] -- drains queue --> R
    W -- inserts --> D
```

### 0. Startup

Before serving anything, the process loads the pricing table and validates it against
the routing catalog. A catalog model with no rate in effect stops the process from
starting, so the gateway never calls a model it can't price.

### 1. Admission

Authenticate, throttle, check the token budget, and short-circuit repeated requests before
any provider is touched.

```mermaid
flowchart TB
    B1["Receive request<br/>open trace span"] --> B2["Validate API key"]
    B2 --> B3{Valid?}
    B3 -- No --> R401["401 UNAUTHORIZED"]
    B3 -- Yes --> B4["Requests per minute"]
    B4 --> B5{Within limit?}
    B5 -- No --> R429["429 RATE_LIMITED"]
    B5 -- Yes --> T1["Estimate prompt tokens<br/>check monthly quota + tokens per minute"]
    T1 --> T2{Within budget?}
    T2 -- No --> R429T["429 TOKEN_QUOTA_EXCEEDED<br/>or RATE_LIMITED"]
    T2 -- Yes --> B6{Idempotency-Key sent?}
    B6 -- No --> NEXT["Continues in:<br/>Routing & Failover"]
    B6 -- Yes --> B7["Reserve key in Redis"]
    B7 --> B8{Reservation outcome}
    B8 -- conflict --> R422["422 IDEMPOTENCY_KEY_CONFLICT"]
    B8 -- in progress --> R409["409 IDEMPOTENCY_IN_PROGRESS"]
    B8 -- "earlier attempt<br/>outcome unknown" --> R409U["409 IDEMPOTENCY_OUTCOME_UNKNOWN"]
    B8 -- completed --> B9["Replay stored response"]
    B8 -- acquired --> NEXT
    B9 --> R200["200 (replay, no provider call)"]
```

**The token budget runs on an estimate.** The real count only exists once the provider
answers, so admission charges the estimated prompt and B17 corrects both the
tokens-per-minute bucket and the monthly counter with the real tokens. Details in
[ratelimit](core/ratelimit.md#token-budget).

### 2. Routing & failover

Try ranked candidates in order; skip the ones that can't serve, fail over on the ones that fail.

```mermaid
flowchart TB
    B10["Resolve routing tier<br/>ranked candidates"] --> B11{Candidate left?}
    B11 -- No --> R503["503 PROVIDER_NOT_AVAILABLE"]
    B11 -- Yes --> B12{Adapter configured?}
    B12 -- No --> B11
    B12 -- Yes --> B13{Circuit closed?}
    B13 -- No --> B11
    B13 -- Yes --> B14["Call provider adapter<br/>with hard timeout"]
    B14 -. "unavailable<br/>(never executed)" .-> B11
    B14 -. "rejected 400/413/422<br/>(caller's fault)" .-> R4XX["provider's status + message<br/>no failover, no breaker failure"]
    B14 -. "timeout / 5xx<br/>(outcome unknown)" .-> B15{Failover policy}
    B15 -- at_least_once --> B11
    B15 -- at_most_once --> R502["502 UPSTREAM_AMBIGUOUS"]
    B14 --> G[AI Provider]
    G --> B16["Receive response<br/>record success on breaker"]
    B16 --> B17["Settle token budget, record usage<br/>real tokens, priced by the catalog model"]
    B17 -. "insert failed" .-> Q[("Redis usage:pending")]
    B17 --> B18["Store under idempotency key<br/>(if one was sent)"]
    Q -.-> B18
    B18 --> B19["Format / return response"]
```

**Why the two dotted edges differ:** an *unavailable* provider provably never ran the
request, so failing over is always safe. A *timeout* is ambiguous the request may have
executed and billed so failing over is gated on the caller's `failover_policy`, which
defaults to `at_most_once` (no double-spend).

**Streaming follows the same diagram up to the first event.** Once a candidate has sent
anything, content may already be with the caller, so a later failure can't fail over:
the stream ends with an error event and what was produced is billed as a partial
(counts estimated when the provider never sent them).

**After B16 the provider has billed.** Everything from there on is our own bookkeeping
and never turns a served request into an error: a failed usage insert goes to the retry
queue, a pricing, budget or Redis failure is logged, and the response is returned and stored
under its idempotency key regardless. So a client retry with the same key is a replay,
never a second billed call.

### 3. Usage retry worker

A separate process (`python -m app.workers.usage_retry`) persists what B17 couldn't.

```mermaid
flowchart TB
    W1["BLMOVE usage:pending<br/>→ usage:processing"] --> W2["Insert<br/>ON CONFLICT (id) DO NOTHING<br/>+ commit"]
    W2 -- ok --> W3["LREM from processing"]
    W2 -- "failed, attempts left" --> W4["Back to pending<br/>wait backoff"]
    W2 -- "failed, max attempts" --> W5[("usage:dead")]
    W3 --> W1
    W4 --> W1
```

Delivery is at least once and the insert dedupes on the record id, so a crash at any
point, or two workers, never counts usage twice. On startup the worker moves entries a
crashed run left in `processing` back to `pending`.
