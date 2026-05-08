# Custom PostgreSQL MCP Server — Requirements Brief

## Context

We need a custom MCP server for PostgreSQL that serves as the
primary database interface for AI agents (Claude Code, Claude
Desktop). The server must handle production analytical workloads
reliably — large exports, complex joins, long-running queries —
without manual intervention from the user.

The server should be a fork of
[crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp),
extending it with the capabilities listed below.

## Requirements

### 1. Query Output Modes

The `query` tool must support three output modes, selected via
parameters:

- **Inline only** (default): results returned in the MCP
  response, same as current behavior.
- **File only**: results streamed to a local CSV file using
  PostgreSQL `COPY TO STDOUT WITH CSV HEADER`. The MCP response
  contains only metadata: file path, row count, byte size, column
  names. No result data in the response.
- **File + inline**: results streamed to file AND returned in the
  MCP response. For cases where the agent needs both a persistent
  file and immediate access to the data.

The agent specifies the mode per query via parameters (e.g.,
`output_file` for the file path, `output_mode` for the
behavior). Results must never be held entirely in memory
regardless of mode — streaming is mandatory for file output.

### 2. Per-Query Timeout

The `query` tool must accept an optional `timeout_ms` parameter.
Applied via `SET LOCAL statement_timeout` within the transaction.
A timed-out query returns a clear error — not a connection drop.
The connection must remain usable after a timeout. Default timeout
configurable at the MCP server level via a CLI argument or
environment variable.

### 3. Automatic Reconnection

When the underlying PostgreSQL connection fails (network drop,
database restart, any connection error), the MCP server must:
- Detect the failure on the next query attempt
- Reconnect automatically with exponential backoff
- Log reconnection events to the event history (see §4)
- Resume normal operation without user intervention
- Never crash the MCP server process due to a connection failure

**Pre-connection hook**: The MCP server accepts an optional
`pre_connect_script` parameter (CLI argument or environment
variable) — a shell command or script path. The MCP server
executes this script before the initial connection and before
each reconnection attempt. The script's job is to ensure the
database is reachable at the connection string address (e.g.,
establish a network tunnel, start a VPN, wake a remote host).
The MCP server waits for the script to exit successfully
(exit code 0) before attempting to connect. If the script
fails, the MCP server logs the error and retries according to
the backoff schedule.

This keeps the MCP server transport-agnostic — it doesn't know
or care what makes the database reachable, only that the hook
script handles it.

### 4. Connection Status Tool

A `status` tool with granular, atomic access to server state.

**Minimal response** (no arguments): current connection state
only — one of: `connected`, `querying`, `reconnecting`, `error`.

**Optional arguments** to request additional detail:

- `errors` (integer, default 3): return the last N error events
  with timestamps.
- `warnings` (integer, default 3): return the last N warning
  events with timestamps.
- `events` (integer, default 3): return the last N regular events
  (reconnections, state transitions) with timestamps.
- `metadata` (boolean, default false): include connection
  metadata — database name, server version, total reconnect
  count, uptime.
- `queries` (integer, default 0): return metadata about the last
  N completed queries — execution time, row count, byte size,
  timed-out flag. **No SQL text or result data** — queries may
  contain sensitive information.

Each event category (errors, warnings, events) maintains its own
ring buffer. Buffer sizes are configurable at the MCP server
level via CLI arguments or environment variables.

### 5. Progress Notifications

For long-running queries and file exports, emit MCP progress
notifications (row count, elapsed time) so the agent can relay
status to the user.

## Configuration

All configurable values must be settable via CLI arguments or
environment variables. No hardcoded defaults that cannot be
overridden. Key configuration points:

- Connection string (required)
- Default query timeout in milliseconds
- Event buffer sizes (per category: errors, warnings, events,
  queries)
- Reconnection parameters (initial backoff, max backoff,
  max attempts or unlimited)
- File output default directory

## Constraints

- Must work via stdio transport (launched as a subprocess)
- Connection string provided as a CLI argument
- Read-only mode enforced at the database role level — the MCP
  server should not override this
- Primary platforms: macOS and Linux. Development and testing
  target these two.
- Windows: design for compatibility (no Unix-only assumptions in
  core logic, use `pathlib` for paths, etc.) but do not test on
  Windows or let Windows support slow down development.
- Python >=3.10 (the upstream fork pins 3.12+ but its
  dependencies — psycopg3, pglast, MCP SDK — all support 3.10+;
  lower the floor to avoid conflicts with other tools that cap
  at 3.12, e.g., ChromaDB)

## Research: Why Fork crystaldba

We evaluated 7 existing MCP PostgreSQL servers against our 6
core requirements. None covers more than 1 out of 6. File output,
per-query timeout, auto reconnection, status tool, pre-connect
hooks, and progress notifications must be custom-built regardless
of which base is chosen.

Full research notebook (96+ web sources, comparison matrix,
deep analysis):
[NotebookLM: SYNC-1418 MCP PostgreSQL Server — Alternative Evaluation](https://notebooklm.google.com/notebook/7d1bb005-298c-49e6-a32d-dee992949194)

### Candidates Evaluated

| Server | Lang | File Output | Timeout | Reconnect | Status | Hooks | Progress |
|--------|------|:-:|:-:|:-:|:-:|:-:|:-:|
| [crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp) | Python | — | partial | — | partial | — | — |
| [postgres-mcp-pro-plus](https://github.com/Cloud-Thinker-AI/postgres-mcp-pro-plus) | Python | — | partial | — | partial | — | — |
| [mcp-run-sql-connectorx](https://github.com/gigamori/mcp-run-sql-connectorx) | Python | **yes** | — | — | — | — | — |
| [pgmcp](https://github.com/subnetmarco/pgmcp) | Go | partial | partial | — | — | — | — |
| [HenkDz/postgresql-mcp-server](https://github.com/HenkDz/postgresql-mcp-server) | TS | partial | — | partial | partial | — | — |
| [pgEdge](https://www.pgedge.com/blog/lessons-learned-writing-an-mcp-server-for-postgresql) | — | — | — | — | — | — | — |
| [@modelcontextprotocol/server-postgres](https://github.com/modelcontextprotocol/servers) | TS | — | — | — | — | — | — |

### Why crystaldba as base

- Async psycopg3 with connection pooling
- SQL safety via pglast (blocks COMMIT/ROLLBACK injection)
- Official MCP Python SDK
- Health diagnostics, explain plans, index tuning included
- Python — matches our runtime constraint

### Reference implementations to borrow from

- **File streaming**: `mcp-run-sql-connectorx` — streams via
  ConnectorX + PyArrow to CSV/Parquet files in RecordBatch
  chunks (100K rows default batch). The only MCP server that
  writes results to disk.
- **Inline format**: pgEdge's TSV approach uses 30–40% fewer
  tokens than JSON for tabular data. Consider TSV instead of
  JSON for inline result mode.
- **Per-tool timeout pattern**: IBM ibmi-mcp-server wraps
  `pool.execute()` with `Promise.race()` for per-query
  timeouts; on timeout, marks pool unhealthy for
  re-initialization.

## Deliverables

- Forked repository with the above features
- Tests covering: file output with 100K+ rows across all three
  output modes, timeout behavior, reconnection after connection
  drop, status tool with all argument combinations, event
  history accuracy
- Documentation for configuration and deployment
