## System flow

The request path is split into two stages: **admission** (is this caller allowed, and
have we already answered this exact request?) and **routing & failover** (which provider
serves it, and what happens when one fails).

### High-level flow

```mermaid
flowchart LR
    A[Main backend] -- "POST /v1/chat/completions" --> B[Relayix Gateway]
    B -- response --> A
    B -- request --> G[AI Provider]
    G -- response --> B
    B <--> R[("Redis<br/>rate limit + idempotency")]
    B --> D[("Postgres<br/>api_keys + usage_records")]
```

### 1. Admission

Authenticate, throttle, and short-circuit repeated requests before any provider is touched.

```mermaid
flowchart TB
    B1["Receive request<br/>tag X-Request-ID"] --> B2["Validate API key"]
    B2 --> B3{Valid?}
    B3 -- No --> R401["401 UNAUTHORIZED"]
    B3 -- Yes --> B4["Rate limit per key"]
    B4 --> B5{Within limit?}
    B5 -- No --> R429["429 RATE_LIMITED"]
    B5 -- Yes --> B6{Idempotency-Key sent?}
    B6 -- No --> NEXT["Continues in:<br/>Routing & Failover"]
    B6 -- Yes --> B7["Reserve key in Redis"]
    B7 --> B8{Reservation outcome}
    B8 -- conflict --> R422["422 IDEMPOTENCY_KEY_CONFLICT"]
    B8 -- in progress --> R409["409 IDEMPOTENCY_IN_PROGRESS"]
    B8 -- completed --> B9["Replay stored response"]
    B8 -- acquired --> NEXT
    B9 --> R200["200 (replay, no provider call)"]
```

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
    B14 -. "timeout<br/>(outcome unknown)" .-> B15{Failover policy}
    B15 -- at_least_once --> B11
    B15 -- at_most_once --> R502["502 UPSTREAM_AMBIGUOUS"]
    B14 --> G[AI Provider]
    G --> B16["Receive response<br/>record success on breaker"]
    B16 --> B17["Record usage<br/>tokens priced into a UsageRecord"]
    B17 --> B18["Store under idempotency key<br/>(if one was sent)"]
    B18 --> B19["Format / return response"]
```

**Why the two dotted edges differ:** an *unavailable* provider provably never ran the
request, so failing over is always safe. A *timeout* is ambiguous — the request may have
executed and billed — so failing over is gated on the caller's `failover_policy`, which
defaults to `at_most_once` (no double-spend).
