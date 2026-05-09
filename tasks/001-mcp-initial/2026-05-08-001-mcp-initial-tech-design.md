# 001-MCP-INITIAL: Fluid PostgreSQL MCP Server — Technical Design

**Status**: Draft
**PRD**: [2026-05-08-001-mcp-initial-prd.md](./2026-05-08-001-mcp-initial-prd.md)
**Created**: 2026-05-08

## Overview

Fork crystaldba/postgres-mcp, restructure the query path to support
file streaming via psycopg3 `COPY TO STDOUT`, add a reconnection
layer inside `DbConnPool`, a `status` tool with ring-buffered event
history, per-query `SET LOCAL statement_timeout`, a pre-connection
hook, and MCP progress notifications via `ctx.report_progress()`.

Remove the `AccessMode.RESTRICTED` / `SafeSqlDriver` query-validation
path. Retain `SafeSqlDriver` as a utility class (its static
`execute_param_query` and `param_sql_to_query` methods are used by
every DBA tool). Retain `pglast` (used directly by index tuning and
bind-param analysis).

Publish as `fluid-postgres-mcp` on PyPI.

## Current Architecture (RLM-verified)

### Module Structure

```
src/postgres_mcp/
├── __init__.py            # entry point: asyncio.run(server.main())
├── server.py              # FastMCP tool registration, CLI parsing
├── artifacts.py           # ErrorResult, ExplainPlanArtifact
├── sql/
│   ├── __init__.py        # re-exports DbConnPool, SqlDriver, SafeSqlDriver, ...
│   ├── sql_driver.py      # DbConnPool, SqlDriver, RowResult, obfuscate_password
│   ├── safe_sql.py        # SafeSqlDriver (pglast validation + param utils)
│   ├── bind_params.py     # SQL parameter binding via pglast AST
│   ├── extension_utils.py # check_extension, version utils
│   └── index.py           # IndexDefinition dataclass
├── explain/               # ExplainPlanTool
├── index/                 # DTA, LLM optimizer, presentation
├── database_health/       # health check calculators
└── top_queries/           # pg_stat_statements analysis
```
— verified via: `find refs/postgres-mcp/src -name "*.py" | sort`, 2026-05-08

### Key Verified Facts

- `DbConnPool.pool_connect()` creates `AsyncConnectionPool(min_size=1,
  max_size=5, open=False)`, then `await pool.open()`, then tests with
  `SELECT 1`. No retry logic. Failure raises `ValueError`.
  — verified via: `refs/postgres-mcp/src/postgres_mcp/sql/sql_driver.py:87-114`,
  2026-05-08

- `SqlDriver.execute_query()` uses `cursor.fetchall()` — all rows
  loaded into memory.
  — verified via: `refs/postgres-mcp/src/postgres_mcp/sql/sql_driver.py:253`,
  2026-05-08

- `SafeSqlDriver._validate()` uses pglast for SQL validation (restricted
  mode only). `SafeSqlDriver.execute_param_query()` and
  `param_sql_to_query()` are **static utility methods** using
  `psycopg.sql.SQL` + `Literal` for parameterized queries — used by
  15+ call sites across server.py, top_queries, explain, index,
  database_health, bind_params, extension_utils.
  — verified via: `grep -rn "SafeSqlDriver" --include="*.py" refs/postgres-mcp/src/`,
  2026-05-08

- `pglast` is imported directly by `dta_calc.py`, `index_opt_base.py`,
  `llm_opt.py`, `bind_params.py` for SQL AST parsing (index tuning,
  column extraction). Not limited to SafeSqlDriver.
  — verified via: `grep -rn "pglast" --include="*.py" refs/postgres-mcp/src/`,
  2026-05-08

- `server.py` uses `AccessMode` enum (UNRESTRICTED/RESTRICTED) to
  conditionally wrap `SqlDriver` in `SafeSqlDriver`. The `execute_sql`
  tool is registered dynamically with different descriptions per mode.
  — verified via: `refs/postgres-mcp/src/postgres_mcp/server.py:49-68,607-624`,
  2026-05-08

- MCP Python SDK `FastMCP` provides `ctx.report_progress(progress,
  total, message)` for progress notifications from within tool
  functions. Context is injected via type hint.
  — verified via: web search, MCP Python SDK docs, 2026-05-08

- psycopg3 `AsyncCopy` supports `COPY (...) TO STDOUT WITH CSV HEADER`
  with async block-by-block iteration for streaming to file.
  — verified via: Context7 psycopg3 docs (copy.html, api/copy.html),
  2026-05-08

### What We Keep vs. Change

| Component | Action | Rationale |
|-----------|--------|-----------|
| `DbConnPool` | **Extend** | Add reconnection + backoff + pre-connect hook |
| `SqlDriver` | **Extend** | Add `execute_to_file()` + `execute_query_with_timeout()` |
| `SafeSqlDriver._validate` | **Remove from query path** | DB role handles access control |
| `SafeSqlDriver.execute_param_query` | **Keep** (static util) | 15+ DBA tool call sites depend on it |
| `SafeSqlDriver.param_sql_to_query` | **Keep** (static util) | Used by explain, index tools |
| `AccessMode` enum | **Remove** | No restricted mode; always unrestricted |
| `pglast` dependency | **Keep** | Index tuning, bind_params use it directly |
| server.py tool registration | **Modify** | Add `status` tool, modify `execute_sql` params |
| All DBA tools | **Keep as-is** | No modifications needed |

## Proposed Design

### Component Architecture

```
┌─────────────────────────────────────────────────┐
│                  server.py                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │execute_sql│  │  status  │  │ DBA tools    │  │
│  │(modified) │  │  (new)   │  │ (unchanged)  │  │
│  └────┬──┬──┘  └────┬─────┘  └──────┬───────┘  │
│       │  │          │               │           │
│       │  │     ┌────┴─────┐         │           │
│       │  │     │EventStore│         │           │
│       │  │     │(new)     │         │           │
│       │  │     └──────────┘         │           │
│       │  │                          │           │
│  ┌────┴──┴──────────────────────────┴────────┐  │
│  │             SqlDriver (extended)           │  │
│  │  execute_query()      — inline mode       │  │
│  │  execute_to_file()    — file/file+inline  │  │
│  │  execute_with_timeout() — SET LOCAL       │  │
│  └────────────────┬──────────────────────────┘  │
│              ┌────┴─────────────┐               │
│              │  DbConnPool      │               │
│              │  (extended)      │               │
│              │  + reconnect     │               │
│              │  + pre-connect   │               │
│              │  + state machine │               │
│              └──────────────────┘               │
└─────────────────────────────────────────────────┘
```

### C1: DbConnPool — Reconnection + Pre-Connect Hook

**Location**: `src/postgres_mcp/sql/sql_driver.py` (extend existing)

**Responsibilities**:
- Connection lifecycle with automatic reconnection
- Pre-connect hook execution
- Connection state tracking
- Reconnection event reporting

**State Machine**:
```
DISCONNECTED ──connect()──→ CONNECTED
CONNECTED ──query fails──→ RECONNECTING
RECONNECTING ──success──→ CONNECTED
RECONNECTING ──max retries──→ ERROR
ERROR ──next query──→ RECONNECTING (restart attempts)
CONNECTED ──query starts──→ QUERYING
QUERYING ──query ends──→ CONNECTED
```

**Reconnection Strategy**:
- Exponential backoff: `min(initial_delay * 2^attempt, max_delay)`
- Configurable: `initial_delay` (default 1s), `max_delay` (default 60s),
  `max_attempts` (default 0 = unlimited)
- Before each attempt: run pre-connect hook if configured
- On connection error during query: catch `psycopg.OperationalError`,
  mark pool invalid, trigger reconnect on next call
- Pool is fully recreated on reconnect (close old → create new) because
  `AsyncConnectionPool` has no built-in reconnect-all

**Pre-Connect Hook**:
- Runs `pre_connect_script` via `asyncio.create_subprocess_exec`
- Timeout for hook execution: configurable (default 30s)
- Hook failure = log error + continue backoff (don't attempt connect)
- Hook stdout/stderr captured and logged at DEBUG level

**Data Contract** (new fields on DbConnPool):
```python
class ConnState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    QUERYING = "querying"
    RECONNECTING = "reconnecting"
    ERROR = "error"

@dataclass
class ReconnectConfig:
    initial_delay: float = 1.0
    max_delay: float = 60.0
    max_attempts: int = 0  # 0 = unlimited
    pre_connect_script: str | None = None
    hook_timeout: float = 30.0
```

### C2: SqlDriver — File Streaming + Timeout

**Location**: `src/postgres_mcp/sql/sql_driver.py` (extend existing)

**New Method: `execute_to_file()`**

Uses psycopg3 `COPY (user_query) TO STDOUT WITH CSV HEADER` for
streaming. Data flows block-by-block from PostgreSQL through
psycopg3's copy protocol directly to a file — never buffered
entirely in memory.

```
PostgreSQL ──COPY blocks──→ psycopg3 AsyncCopy ──write()──→ file
                                    │
                            progress callback
                                    │
                              ctx.report_progress()
```

**Row counting**: `COPY TO STDOUT` sends data in raw text blocks,
not discrete rows. Row count is derived from counting newline
characters in each block. This is an approximation for fields
containing embedded newlines, but accurate for typical analytical
data. The exact row count is available from PostgreSQL's
`cursor.statusmessage` after COPY completes (format:
`COPY <row_count>`). Use `statusmessage` for the final count in
the response metadata.

**Byte counting**: Accumulated from `len(data)` on each block
written.

**file+inline mode**: For `file+inline`, the query runs twice:
once via `COPY TO STDOUT` for the file, once via normal
`execute_query` for inline data. This is the simplest correct
approach. The alternative (tee-ing COPY blocks while also parsing
them) adds complexity for an uncommon mode. Two executions is
acceptable because `file+inline` is for moderate result sets where
the agent needs both a file and immediate data access — not for
million-row exports.

[assumption, verify during implementation: `file+inline` two-query
approach is acceptable for typical use cases; revisit if performance
is a concern]

**New Method: `execute_with_timeout()`**

Wraps query execution in a transaction with `SET LOCAL
statement_timeout`:

```
BEGIN
SET LOCAL statement_timeout = '{timeout_ms}'
<user query>
COMMIT / ROLLBACK on error
```

`SET LOCAL` scopes the timeout to the current transaction only —
doesn't affect other connections in the pool. On timeout,
PostgreSQL raises `QueryCanceled` which psycopg3 translates to
`psycopg.errors.QueryCanceled`. Catch this, return a clear error,
connection remains usable.

The existing `execute_query()` gains an optional `timeout_ms`
parameter. When set, it delegates to `execute_with_timeout()`.
When unset, falls back to server-level default (if configured) or
no timeout.

### C3: EventStore — Ring-Buffered Event History

**Location**: `src/postgres_mcp/event_store.py` (new file)

**Responsibilities**:
- Store events in per-category ring buffers
- Provide query interface for the `status` tool
- Track query metadata (no SQL text)

**Data Contracts**:
```python
class EventCategory(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    EVENT = "event"
    QUERY = "query"

@dataclass
class Event:
    timestamp: datetime
    category: EventCategory
    message: str

@dataclass
class QueryRecord:
    timestamp: datetime
    duration_ms: float
    row_count: int
    byte_size: int
    timed_out: bool
    output_mode: str  # inline | file | file+inline

class EventStore:
    def __init__(self, buffer_sizes: dict[EventCategory, int]):
        ...
    def record(self, category: EventCategory, message: str): ...
    def record_query(self, record: QueryRecord): ...
    def get_events(self, category: EventCategory, n: int) -> list[Event]: ...
    def get_queries(self, n: int) -> list[QueryRecord]: ...
```

**Buffer sizes**: configurable via CLI/env. Defaults: errors=50,
warnings=50, events=100, queries=100.

### C4: Status Tool

**Location**: `src/postgres_mcp/server.py` (new tool registration)

**Interface**:
```python
@mcp.tool(
    description="Get connection status and server event history",
    annotations=ToolAnnotations(title="Connection Status", readOnlyHint=True),
)
async def status(
    errors: int = 3,
    warnings: int = 3,
    events: int = 3,
    metadata: bool = False,
    queries: int = 0,
) -> ResponseType:
```

**Metadata** (when `metadata=True`):
- Database name, server version — from `SELECT current_database(),
  version()`; cached after first successful connection, refreshed
  on reconnect
- Total reconnect count — from `DbConnPool`
- Uptime — time since MCP server process start

### C5: Modified execute_sql Tool

**Location**: `src/postgres_mcp/server.py` (modify existing)

**New Parameters**:
```python
async def execute_sql(
    sql: str,
    output_file: str | None = None,    # file path for output
    output_mode: str = "inline",        # inline | file | file+inline
    timeout_ms: int | None = None,      # per-query timeout
) -> ResponseType:
```

**Tool Registration**: Always registered as unrestricted (no
`AccessMode` branching). `readOnlyHint` set based on future
consideration; initially `False` to match upstream unrestricted.

**Response Formats**:

*Inline mode* (default): unchanged from upstream — list of
`RowResult.cells` dicts.

*File mode*: metadata-only response:
```json
{
  "file": "/path/to/output.csv",
  "rows": 500000,
  "bytes": 42000000,
  "columns": ["id", "name", "value"]
}
```

*File+inline mode*: both file metadata and inline results.

### C6: Progress Notifications

**Location**: `src/postgres_mcp/server.py` (within execute_sql)

**Mechanism**: `ctx.report_progress(progress, total, message)` from
the MCP Python SDK. The `Context` object is injected into tool
functions via type hint.

**When emitted**:
- **File mode**: after every N bytes written (N = adaptive, targeting
  ~2-5 notifications for typical exports). Report: rows written,
  bytes written, elapsed seconds.
- **Inline mode**: not emitted (results are small enough to return
  quickly; if they aren't, the agent should use file mode).

**Notification interval logic**: emit at 10%, 25%, 50%, 75%, 90%
of estimated total if total is known (via `EXPLAIN` row estimate
before COPY). If total unknown, emit every 100K rows or 10MB,
whichever comes first.

[assumption, verify during implementation: `EXPLAIN` row estimate
before COPY is fast enough to be worth the overhead]

### C7: Configuration

**CLI Arguments** (added to argparse in `server.py`):

| Argument | Env Var | Default | Description |
|----------|---------|---------|-------------|
| `--default-timeout` | `PGMCP_DEFAULT_TIMEOUT_MS` | `0` | Default query timeout (ms), 0=none |
| `--reconnect-initial-delay` | `PGMCP_RECONNECT_INITIAL_DELAY` | `1.0` | Initial backoff (seconds) |
| `--reconnect-max-delay` | `PGMCP_RECONNECT_MAX_DELAY` | `60.0` | Max backoff (seconds) |
| `--reconnect-max-attempts` | `PGMCP_RECONNECT_MAX_ATTEMPTS` | `0` | Max reconnect attempts, 0=unlimited |
| `--pre-connect-script` | `PGMCP_PRE_CONNECT_SCRIPT` | `None` | Script to run before connecting |
| `--hook-timeout` | `PGMCP_HOOK_TIMEOUT` | `30` | Pre-connect hook timeout (seconds) |
| `--event-buffer-size` | `PGMCP_EVENT_BUFFER_SIZE` | `100` | Ring buffer size per category |
| `--output-dir` | `PGMCP_OUTPUT_DIR` | `.` | Default directory for file output |

Existing upstream args retained: `database_url`, `--transport`,
`--sse-host`, `--sse-port`, `--streamable-http-host`,
`--streamable-http-port`.

Removed: `--access-mode` (no restricted mode).

## Removals

### AccessMode / SafeSqlDriver Validation Path

**What's removed**:
- `AccessMode` enum and `current_access_mode` global
- `get_sql_driver()` function (returns `SqlDriver` directly now)
- `--access-mode` CLI argument
- `SafeSqlDriver._validate()` and `_validate_node()` (SQL AST
  validation logic)
- Conditional tool registration based on access mode

**What's kept from SafeSqlDriver**:
- `execute_param_query()` — static method, used by 15+ DBA tool
  call sites
- `param_sql_to_query()` — static method, used by explain/index
- `sql_to_query()` — static method

These static methods are pure utility functions (SQL parameterization
via `psycopg.sql`). They don't invoke `_validate()` and don't depend
on SafeSqlDriver being used as a query driver. They remain importable
from `postgres_mcp.sql`.

**Refactoring approach**: Keep `SafeSqlDriver` class but remove the
`_validate`, `_validate_node`, `execute_query` (override), and timeout
logic. Rename to `SqlParamUtils` or similar if clarity demands it, but
renaming means updating 15+ import sites. Pragmatic choice: keep the
class name `SafeSqlDriver` in v1 to minimize diff from upstream,
enabling cherry-picks. Document that the name is a misnomer.

## Trade-offs

### Reconnection Inside DbConnPool vs. New Wrapper

**Chosen: Extend DbConnPool**

- Pros: Minimal changes to call sites. `SqlDriver` and DBA tools
  already hold a `DbConnPool` reference; reconnection is transparent.
  Single source of connection state.
- Cons: `DbConnPool` grows in responsibility (connection + reconnect
  + hook + state). Could be split later if it becomes unwieldy.
- Rejected alternative: New `ConnectionManager` wrapping `DbConnPool`.
  Adds indirection, requires every call site to go through the manager
  instead of directly using the pool. Not justified at current
  complexity level.

### COPY TO STDOUT vs. fetchmany + csv.writer

**Chosen: COPY TO STDOUT**

- Pros: PostgreSQL handles serialization server-side — faster, lower
  memory, lower CPU on client. No row-level Python overhead. Psycopg3
  async copy protocol supports block-by-block streaming.
  — verified via: Context7 psycopg3 docs (copy.html), 2026-05-08
- Cons: PostgreSQL-specific (we are PostgreSQL-specific). Output is
  always CSV from COPY (can't easily do Parquet — but Parquet is out
  of scope). Row count requires parsing `statusmessage` or counting
  newlines (minor).
- Rejected alternative: `fetchmany()` batches + `csv.writer`. Higher
  memory (batch of dicts in Python), slower (Python serialization per
  row), more code. Only advantage: easier row counting.

### file+inline via Two Queries vs. Tee

**Chosen: Two queries**

- Pros: Simple, correct, each path tested independently.
- Cons: Query executes twice. Acceptable for `file+inline` use case
  (moderate result sets where agent needs both).
- Rejected alternative: Tee COPY output while also parsing into
  RowResult dicts. Complex, fragile, error-prone with async streams.
  Not justified for an uncommon mode.

### Keep SafeSqlDriver Name vs. Rename

**Chosen: Keep name, document the misnomer**

- Pros: Zero-diff for 15+ import sites. Easier cherry-picks from
  upstream.
- Cons: Name implies SQL safety that no longer exists.
- Rejected alternative: Rename to `SqlParamUtils`. 15+ files to
  update, larger diff, harder to merge upstream changes.

## Verification Approach

| Requirement | Method | Scope | Expected Evidence |
|-------------|--------|-------|-------------------|
| FR-1: Query Output Modes | `auto-test` | integration | pytest: 3 modes produce correct output (inline, file with metadata, file+inline with both) |
| FR-1: 500K-row file export | `auto-test` | integration | pytest against k8s PG (pgmcp-test namespace): CSV file valid, memory <50 MB |
| FR-2: Per-query timeout | `auto-test` | integration | pytest: `pg_sleep(10)` with timeout_ms=1000 raises error in ~1s, next query succeeds |
| FR-3: Auto reconnection | `auto-test` | integration | pytest against k8s PG: `pg_terminate_backend()`, next query triggers reconnect and succeeds |
| FR-4: Pre-connect hook | `auto-test` | unit + integration | pytest: mock script called before connect, failure triggers backoff |
| FR-5: Status tool | `auto-test` | unit | pytest: status returns correct state after connect/query/error/reconnect |
| FR-6: Progress notifications | `manual-run-claude` | integration | MCP client receives ≥2 progress notifications during large export |
| FR-7: DBA tools retained | `auto-test` | unit | pytest: upstream DBA tool tests pass without modification |
| NFR-3: Never crash on conn failure | `auto-test` | integration | Server process stays alive after connection drop, returns error, reconnects |
| NFR-5: No credentials in output | `auto-test` | unit | pytest: status/error responses contain no connection string fragments |

## Files to Create

- `src/postgres_mcp/event_store.py` — EventStore, Event, QueryRecord,
  ring buffer implementation
- `src/postgres_mcp/config.py` — ReconnectConfig, ServerConfig
  dataclasses, CLI/env parsing helpers

## Files to Modify

- `src/postgres_mcp/sql/sql_driver.py` — DbConnPool: add reconnect
  loop, state machine, pre-connect hook. SqlDriver: add
  `execute_to_file()`, `execute_with_timeout()`
- `src/postgres_mcp/server.py` — Remove AccessMode. Add `status` tool.
  Modify `execute_sql` params (output_file, output_mode, timeout_ms).
  Add progress notifications. Update CLI args.
- `src/postgres_mcp/__init__.py` — Remove `top_queries` import if not
  needed at package level
- `pyproject.toml` — Rename to `fluid-postgres-mcp`, update version,
  requires-python to `>=3.10`, update entry point, add project URLs

## Dependencies

**Kept from upstream**:
- `mcp[cli]>=1.25.0` — MCP server framework
- `psycopg[binary]>=3.3.2` — PostgreSQL adapter (COPY, async)
- `psycopg-pool>=3.3.0` — AsyncConnectionPool
- `pglast==7.11` — SQL parsing (DBA tools)
- `humanize>=4.15.0` — Human-readable sizes
- `attrs>=25.4.0` — Dataclasses
- `instructor>=1.14.4` — LLM index optimization

**Removed**:
- None (pglast kept for DBA tools)

**Added**:
- None (all capabilities from psycopg3 + stdlib)

## Security Considerations

- Connection string obfuscation retained (`obfuscate_password()`).
  Applied to all error messages and log output
- `status` tool: query metadata includes duration/row count/byte size
  but **never** SQL text or result data
- Pre-connect hook: runs with same privileges as MCP server process.
  Script path must be explicitly configured — no default
- File output: written to configured `--output-dir`. No path traversal
  validation beyond what the OS provides (agent controls the path)
  [assumption, verify during implementation: whether path validation
  is needed given the agent is the only caller]

## Rollback Plan

The fork is a new repository. Rollback = point the MCP client config
back to upstream `postgres-mcp`. No shared state to migrate.

---

**Next Steps**:
1. Review and approve this design
2. Run `/dev:tasks` for task breakdown
3. Begin with C1 (DbConnPool reconnection) as it's the foundation
   for all other features
