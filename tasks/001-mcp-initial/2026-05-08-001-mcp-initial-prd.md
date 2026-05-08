# 001-MCP-INITIAL: Fluid PostgreSQL MCP Server — PRD

**Status**: Draft
**Created**: 2026-05-08
**Author**: Claude (via dev workflow analysis)

---

## Context

AI agents (Claude Code, Claude Desktop) need a reliable PostgreSQL
interface for production analytical workloads — large exports, complex
joins, long-running queries. No existing MCP PostgreSQL server covers
more than one of the six required capabilities.

### Current State (observed)

- crystaldba/postgres-mcp v0.3.0 provides async psycopg3 with
  connection pooling, pglast-based SQL safety, health diagnostics,
  explain plans, index tuning, and top-queries reporting
  — verified via: `refs/postgres-mcp/pyproject.toml:1-15`,
  `refs/postgres-mcp/src/postgres_mcp/server.py`, 2026-05-08
- crystaldba uses `AsyncConnectionPool` (psycopg-pool) with min_size=1,
  max_size=5, `dict_row` cursor factory
  — verified via: `refs/postgres-mcp/src/postgres_mcp/sql/sql_driver.py:88-95`,
  2026-05-08
- crystaldba query execution fetches all rows into memory via
  `cursor.fetchall()` — no streaming, no file output
  — verified via: `refs/postgres-mcp/src/postgres_mcp/sql/sql_driver.py:253`,
  2026-05-08
- crystaldba has no reconnection logic — pool creation failure raises
  `ValueError` and connection is not retried
  — verified via: `refs/postgres-mcp/src/postgres_mcp/sql/sql_driver.py:107-114`,
  2026-05-08
- crystaldba timeout is Python-side `asyncio.timeout` in SafeSqlDriver
  (30s hardcoded), not PostgreSQL `statement_timeout`
  — verified via: `refs/postgres-mcp/src/postgres_mcp/sql/safe_sql.py:868,992`,
  2026-05-08
- mcp-run-sql-connectorx streams query results to CSV/Parquet via
  PyArrow RecordBatch chunks (100K default batch size), writing
  incrementally without holding full result in memory
  — verified via: `refs/mcp-run-sql-connectorx/src/run_sql_connectorx/server.py:57-93`,
  2026-05-08
- Seven MCP PostgreSQL servers evaluated; none implements more than 1
  of 6 requirements — verified via: claude-mem #12677, 2026-05-08
- Project name "fluid-postgres-mcp" / "pgmcp-fluid" validated as
  unique across GitHub, PyPI, npm
  — verified via: claude-mem #12682, 2026-05-08

### Decisions Already Made

- **Base**: Fork crystaldba/postgres-mcp for its architecture,
  DBA tooling, and psycopg3 foundation
- **Python**: >=3.10 (lowered from upstream's 3.12+ for ecosystem
  compatibility) — claude-mem #12672
- **Access control**: DB-role-level enforcement only; SafeSqlDriver
  (pglast validation) removed from the fork. The server trusts the
  database role's permissions
- **DBA features**: Retained — explain, index tuning, health checks,
  top queries all kept from upstream
- **Transport**: All upstream transports retained (stdio, SSE,
  streamable-http)

## Problem Statement

**Who**: AI agents operating as database analysts or data engineers
**What**: Cannot export large result sets to files, have no visibility
into query progress or connection state, and lose the session when the
database connection drops
**Why**: Current MCP PostgreSQL servers return all data inline (memory
exhaustion on large queries), have no reconnection logic (agent must
be restarted), and provide no status visibility (agent cannot
diagnose failures)
**When**: During production analytical sessions — ad-hoc reporting,
data exploration, ETL validation — where queries routinely return
100K+ rows and sessions last hours

## Goals

### Primary Goal

Provide AI agents with a PostgreSQL MCP server that handles production
analytical workloads end-to-end without manual intervention: large
result streaming to files, per-query timeouts, automatic reconnection,
connection status visibility, and progress reporting.

### Secondary Goals

- Maintain compatibility with the upstream crystaldba feature set
  (DBA tools remain functional)
- Enable deployment in environments requiring network tunnels or VPNs
  via pre-connection hooks
- Keep the server lightweight enough to run as a subprocess alongside
  other MCP servers

## User Stories

### Epic

As an AI agent, I want a PostgreSQL MCP server that reliably handles
production analytical workloads, so that I can complete data tasks
without manual intervention from the user.

### US-1: Stream Large Results to File

**As an** AI agent performing data exports
**I want** to stream query results directly to a CSV file on disk
**So that** I can handle result sets of any size without exhausting
memory or the MCP response payload

**Acceptance Criteria**:
- [ ] Query tool accepts an output file path parameter
- [ ] Query tool accepts an output mode parameter: `inline` (default),
  `file`, or `file+inline`
- [ ] In `file` mode, results stream to disk; the MCP response contains
  only metadata (file path, row count, byte size, column names)
- [ ] In `file+inline` mode, results stream to disk AND are returned
  in the MCP response
- [ ] File output uses streaming — memory usage stays bounded
  regardless of result set size
- [ ] A query returning 500K rows in `file` mode completes
  successfully and produces a valid CSV file
- [ ] An empty result set in `file` mode produces a file with only a
  CSV header row

### US-2: Per-Query Timeout

**As an** AI agent running analytical queries
**I want** to set a timeout for individual queries
**So that** runaway queries don't block the session indefinitely and I
can retry with a different approach

**Acceptance Criteria**:
- [ ] Query tool accepts an optional `timeout_ms` parameter
- [ ] Timeout is applied via `SET LOCAL statement_timeout` within the
  query's transaction, not Python-side
- [ ] A timed-out query returns a clear error message (not a
  connection drop or generic exception)
- [ ] The connection remains usable after a timeout — subsequent
  queries execute normally
- [ ] A server-level default timeout is configurable via CLI argument
  or environment variable
- [ ] Timeout of 0 means no timeout (PostgreSQL default)

### US-3: Automatic Reconnection

**As an** AI agent in a long-running session
**I want** the MCP server to automatically reconnect when the database
connection drops
**So that** my session survives transient network failures and
database restarts without user intervention

**Acceptance Criteria**:
- [ ] Connection failure is detected on the next query attempt
- [ ] Reconnection uses exponential backoff
- [ ] Reconnection parameters (initial delay, max delay, max attempts)
  are configurable via CLI or environment variables
- [ ] Reconnection events are logged to the event history ring buffer
- [ ] The MCP server process never crashes due to a connection failure
- [ ] After successful reconnection, queries execute normally
- [ ] If max attempts exhausted, the error is reported clearly and the
  server remains running (can retry later)

### US-4: Pre-Connection Hook

**As a** user deploying the MCP server in a network-restricted
environment
**I want** to run a script before each connection attempt
**So that** the script can establish tunnels, VPNs, or other network
prerequisites before the server tries to connect

**Acceptance Criteria**:
- [ ] Server accepts an optional `pre_connect_script` parameter (CLI
  argument or environment variable)
- [ ] Script executes before the initial connection and before each
  reconnection attempt
- [ ] Server waits for the script to exit with code 0 before
  connecting
- [ ] Script failure (non-zero exit) is logged and the reconnection
  backoff schedule continues
- [ ] Script path supports both absolute paths and executable names
  (PATH lookup)
- [ ] No script configured = no-op (default behavior)

### US-5: Connection Status Tool

**As an** AI agent diagnosing connection issues
**I want** to query the MCP server's connection state and event history
**So that** I can understand what happened, report it to the user, and
decide whether to retry

**Acceptance Criteria**:
- [ ] A `status` tool is available alongside `query`
- [ ] With no arguments, returns current state: one of `connected`,
  `querying`, `reconnecting`, `error`
- [ ] Optional `errors` parameter (integer): returns last N error
  events with timestamps
- [ ] Optional `warnings` parameter (integer): returns last N warning
  events with timestamps
- [ ] Optional `events` parameter (integer): returns last N regular
  events (reconnections, state transitions) with timestamps
- [ ] Optional `metadata` parameter (boolean): includes connection
  metadata — database name, server version, total reconnect count,
  uptime
- [ ] Optional `queries` parameter (integer): returns metadata about
  last N completed queries — execution time, row count, byte size,
  timed-out flag. **No SQL text or result data**
- [ ] Each event category has its own ring buffer with configurable
  size

### US-6: Progress Notifications

**As an** AI agent running long queries or exports
**I want** to receive progress updates during execution
**So that** I can relay status to the user and detect stalled queries

**Acceptance Criteria**:
- [ ] Long-running queries emit MCP progress notifications
- [ ] Notifications include row count processed and elapsed time
- [ ] File exports report rows written and bytes written
- [ ] Notification frequency is reasonable (not every row, not too
  infrequent) [assumption, verify in tech-design]

## Requirements

### Functional Requirements

1. **FR-1**: Query Output Modes
   - **Priority**: High
   - **Rationale**: Enables agents to handle result sets of any size.
     Current inline-only approach fails on large datasets
   - **Scope**: Three modes (inline, file, file+inline) selected per
     query. File output streams to CSV. Metadata-only response in
     file mode

2. **FR-2**: Per-Query Timeout
   - **Priority**: High
   - **Rationale**: Prevents runaway queries from blocking the agent
     session. PostgreSQL-level timeout is more reliable than
     Python-side timeout
   - **Scope**: `timeout_ms` parameter on query tool, applied via
     `SET LOCAL statement_timeout`. Server-level default configurable

3. **FR-3**: Automatic Reconnection
   - **Priority**: High
   - **Rationale**: Long analytical sessions are common; connection
     drops are inevitable. Without reconnection, the user must
     restart the agent
   - **Scope**: Exponential backoff with configurable parameters.
     Never crash the server process

4. **FR-4**: Pre-Connection Hook
   - **Priority**: Medium
   - **Rationale**: Production databases often require network setup
     (SSH tunnels, VPN). The hook keeps the server transport-agnostic
   - **Scope**: Shell script/command executed before connect/reconnect.
     Exit code 0 = proceed, non-zero = retry per backoff schedule

5. **FR-5**: Connection Status Tool
   - **Priority**: High
   - **Rationale**: Agents need to diagnose failures and decide
     whether to retry. Blind retries waste time
   - **Scope**: Granular status tool with optional detail parameters.
     Ring-buffered event history per category

6. **FR-6**: Progress Notifications
   - **Priority**: Medium
   - **Rationale**: Users expect feedback during long operations.
     Agents need progress signals to detect stalls
   - **Scope**: MCP progress notifications with row count and elapsed
     time. Emitted during query execution and file export

7. **FR-7**: DBA Tools Retention
   - **Priority**: Low (inherited, not new development)
   - **Rationale**: Valuable for agents performing database
     optimization tasks
   - **Scope**: Keep upstream's explain, index tuning, health checks,
     top queries as-is. No modifications planned

### Non-Functional Requirements

1. **NFR-1**: Memory — file output mode must use bounded memory
   regardless of result set size. Target: <50 MB overhead for a
   1M-row export [assumption, verify in tech-design]
2. **NFR-2**: Latency — per-query timeout overhead (SET LOCAL +
   transaction management) must add <10 ms to query execution
3. **NFR-3**: Reliability — the MCP server process must never exit
   due to a database connection failure
4. **NFR-4**: Compatibility — Python >=3.10, macOS and Linux primary
   targets. Windows: compatible design (pathlib, no Unix-only
   assumptions) but not tested
5. **NFR-5**: Security — no credentials in logs or MCP responses.
   Upstream's password obfuscation retained. Query text excluded from
   status tool output

### Technical Constraints

- Must work via stdio transport (primary), SSE and streamable-http
  also supported
- Connection string provided as CLI argument or environment variable
- Read-only enforcement delegated to database role — no application-
  level SQL filtering (SafeSqlDriver removed)
- Fork of crystaldba/postgres-mcp — must maintain upstream's module
  structure where practical to enable future cherry-picks
- All configuration via CLI arguments or environment variables; no
  config files

## Out of Scope

- **Parquet output**: CSV only for file output in v1. Parquet can be
  added later if needed
- **Write operations**: No INSERT/UPDATE/DELETE tooling. Read-only by
  design
- **Multi-database**: One connection string per server instance. Run
  multiple instances for multiple databases
- **Authentication management**: Server receives a connection string;
  does not manage credentials, rotate tokens, or handle OAuth flows
- **Windows testing**: Compatible code but no CI or manual testing on
  Windows
- **SafeSqlDriver / pglast validation**: Removed; access control is
  the database role's responsibility
- **Result format options**: Inline results use upstream's JSON format.
  TSV optimization deferred to a future iteration

## Success Metrics

1. **File export**: A 500K-row query completes in `file` mode,
   producing a valid CSV, with server memory staying under a defined
   bound
2. **Reconnection**: After a simulated connection drop (pg_terminate_
   backend), the server reconnects automatically and the next query
   succeeds without user intervention
3. **Timeout**: A query with `timeout_ms=1000` against a
   `pg_sleep(10)` returns a timeout error within ~1 second and the
   connection remains usable
4. **Status**: The status tool returns accurate state and event
   history after a sequence of queries, errors, and reconnections
5. **Progress**: A long-running file export emits at least 2 progress
   notifications before completing

## References

### From Upstream Codebase (refs/postgres-mcp)

- `src/postgres_mcp/server.py` — main server, tool registration,
  FastMCP usage pattern
- `src/postgres_mcp/sql/sql_driver.py` — `DbConnPool`,
  `AsyncConnectionPool`, `SqlDriver`, `RowResult`
- `src/postgres_mcp/sql/safe_sql.py` — `SafeSqlDriver` (to be
  removed), pglast validation
- `pyproject.toml` — dependencies, entry point, tooling config

### From Reference Implementation (refs/mcp-run-sql-connectorx)

- `src/run_sql_connectorx/server.py` — RecordBatch streaming to
  CSV/Parquet, batch writer pattern, stderr capture for Rust panics

### From Research (Claude-Mem)

- #12677: Hybrid fork strategy — crystaldba base + streaming from
  mcp-run-sql-connectorx
- #12678: 70 sources on MCP server implementations — IBM ibmi-mcp
  for timeout/progress patterns
- #12672: Python >=3.10 decision rationale
- #12682: Name validation — "pgmcp-fluid" / "fluid-postgres-mcp"

---

**Next Steps**:
1. Review and refine this PRD
2. Run `/dev:tech-design` to create technical design
3. Run `/dev:tasks` to break down into tasks
