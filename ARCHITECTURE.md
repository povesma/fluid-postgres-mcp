# Architecture

How connection stability, reconnection, and visibility work in
`fluid-postgres-mcp`. For the test methodology (what we break and how),
see [`TESTING-METHODOLOGY.md`](./TESTING-METHODOLOGY.md).

## Component overview

```mermaid
flowchart LR
    P[DbConnPool] -->|on_event| ES[(EventStore<br/>per-category ring buffer)]
    M[ConnectionScriptManager] -->|on_event| ES
    P -->|ensure_ready| M
    M -->|spawn| S[pre-connect script]
    ES --> ST[status MCP tool]
    P --> DB[(Postgres)]
```

## Connection-state machine

```mermaid
stateDiagram-v2
    [*] --> DISCONNECTED
    DISCONNECTED --> CONNECTING: pool_connect()
    CONNECTING --> CONNECTED: success
    CONNECTING --> ERROR: failure
    CONNECTED --> RECONNECTING: mark_invalid()
    RECONNECTING --> CONNECTED: backoff + retry succeeds
    RECONNECTING --> ERROR: max_attempts exhausted
    ERROR --> RECONNECTING: next ensure_connected
    CONNECTED --> [*]: close()
```

Two trigger paths into `mark_invalid()`:

```mermaid
flowchart LR
    Q[next query] -->|libpq error| MI[mark_invalid]
    SE[script exit] -->|watcher fires <1s| MI
    MI --> RL[reconnect loop]
```

## Reconnect loop

The reconnect *loop* is separate from the *trigger*. Both reactive
(next query notices a dead conn) and proactive (script-exit watcher)
paths feed the same loop:

```mermaid
sequenceDiagram
    autonumber
    participant Q as Query / Watcher
    participant P as DbConnPool
    participant M as ConnectionScriptManager
    participant S as pre-connect script
    participant DB as Postgres

    Q->>P: mark_invalid()
    loop until success or max_attempts
        P->>P: backoff (exp.)
        P->>M: ensure_ready()
        alt long-running script alive
            M-->>P: ScriptOutcome(reuse, db_url?)
        else respawn needed
            M->>S: spawn
            S-->>M: [MCP] DB_URL ... / READY_TO_CONNECT
            M-->>P: ScriptOutcome(success, db_url_override?)
        end
        P->>DB: _create_pool(url_override or original)
        DB-->>P: connected
    end
```

## Pre-connect script protocol

Mode is **inferred**, never declared:

```mermaid
flowchart TD
    spawn[spawn script] --> race{first event?}
    race -->|"exits with code 0"| RAE_OK[run-and-exit ✓]
    race -->|"exits with non-zero"| RAE_FAIL[run-and-exit ✗]
    race -->|"&#91;MCP&#93; READY_TO_CONNECT"| LR[long-running ✓ — keep alive]
    race -->|"hook_timeout"| KILL[kill + ready timeout ✗]
```

Long-running re-readiness — the `asyncio.Event` is cleared after each
return so the next `ensure_ready()` awaits a fresh signal:

```mermaid
sequenceDiagram
    participant P as Pool
    participant M as Manager
    participant S as Script (alive)
    P->>M: ensure_ready() #1
    S-->>M: [MCP] READY_TO_CONNECT
    M-->>P: success (event cleared)
    Note over P,M: ... time passes, disconnect happens ...
    P->>M: ensure_ready() #2
    Note over M: awaits next READY (with hook_timeout)
    S-->>M: [MCP] READY_TO_CONNECT
    M-->>P: success (event cleared)
```

## Visibility — `status` + `EventStore`

Every lifecycle transition emits an event into a bounded ring-buffer
keyed by category. The `status` MCP tool reads from it:

```mermaid
flowchart LR
    P[DbConnPool] -->|on_event| ES[(EventStore)]
    M[ConnectionScriptManager] -->|on_event| ES
    ES --> ST[status tool]
    ST --> U[user / Claude Code]
```

Two layers of credential protection in emitted events:

```mermaid
flowchart TD
    URL[URL appears in event] --> P{constructed how?}
    P -->|from parsed components| HD[host + path only<br/>password structurally unreachable]
    P -->|interpolated string| OB[obfuscate_password regex]
    HD --> EV[emitted event]
    OB --> EV
```
