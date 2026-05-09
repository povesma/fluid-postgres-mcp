# 001-mcp-initial - Task List

## Relevant Files

- [tasks/001-mcp-initial/2026-05-08-001-mcp-initial-tech-design.md](
  ./2026-05-08-001-mcp-initial-tech-design.md) ::
  Technical Design
- [tasks/001-mcp-initial/2026-05-08-001-mcp-initial-prd.md](
  ./2026-05-08-001-mcp-initial-prd.md) ::
  Product Requirements Document
- `src/postgres_mcp/sql/sql_driver.py` :: DbConnPool + SqlDriver —
  core connection and query logic (extend)
- `src/postgres_mcp/sql/safe_sql.py` :: SafeSqlDriver — strip
  validation, keep param utils
- `src/postgres_mcp/server.py` :: FastMCP tool registration, CLI
  args (modify)
- `src/postgres_mcp/event_store.py` :: EventStore + ring buffers
  (create)
- `src/postgres_mcp/config.py` :: ServerConfig + ReconnectConfig
  (create)
- `pyproject.toml` :: Package rename, deps, Python version
- `tests/unit/sql/test_sql_driver.py` :: Tests for DbConnPool,
  SqlDriver extensions
- `tests/unit/test_event_store.py` :: Tests for EventStore (create)
- `tests/unit/test_config.py` :: Tests for config parsing (create)
- `tests/integration/test_file_output.py` :: File streaming tests
  (create)
- `tests/integration/test_reconnect.py` :: Reconnection tests
  (create)
- `tests/integration/test_timeout.py` :: Timeout tests (create)
- `tests/integration/test_pre_connect.py` :: Pre-connect hook tests
  (create)
- `tests/integration/test_status.py` :: Status tool e2e tests (create)
- `tests/k8s_fixtures.py` :: Helm-based PostgreSQL lifecycle for k8s
  integration tests (create)
- `tests/e2e/mcp_client_fixtures.py` :: MCP stdio client fixture
  for E2E tests (create)
- `tests/e2e/test_cli_wiring.py` :: CLI arg → config → runtime
  wiring tests (create)
- `tests/e2e/test_mcp_execute_sql.py` :: execute_sql tool via MCP
  protocol in all output modes (create)
- `tests/e2e/test_mcp_status.py` :: status tool via MCP protocol
  with event history (create)
- `tests/e2e/test_server_lifecycle.py` :: Server boot/shutdown
  lifecycle tests (create)
- `tests/e2e/ssm_fixtures.py` :: SSM tunnel + EC2 disruption
  helpers (create)
- `tests/e2e/test_ssm_disruption.py` :: Production-realistic
  disruption tests via SSM (create)

## Notes

- Upstream repo is cloned at `refs/postgres-mcp/` for reference.
  Copy source files into `src/` as the fork baseline before
  modifying.
- Tests use pytest + pytest-asyncio. Run with `pytest` from repo
  root.
- Integration tests require a running PostgreSQL instance (Docker
  or local).
- TDD applies to all logic components. Scaffolding and config
  tasks skip TDD.

## TDD Planning Guidelines

- **Test External Functions Only:** Tests should interact with
  public APIs, exported functions, and external interfaces.
- **Focus on Functionality:** Tests should verify behavior and
  functionality, not internal implementation details.
- **Module-Level Testing:** Tests should cover modules as cohesive
  units.
- **TDD When Feasible:** Apply TDD for business logic (reconnect,
  timeout, streaming, event store). Skip TDD for scaffolding,
  config files, and package metadata.

## Tasks

- [X] 1.0 **User Story:** As a developer, I want to fork and
  scaffold the project so that I have a working baseline to
  build on [6/6]
  - [X] 1.1 Copy upstream `src/postgres_mcp/` from
    `refs/postgres-mcp/src/` into project root `src/`.
    Copy `tests/` directory. Copy `pyproject.toml`.
    [verify: code-only]
  - [X] 1.2 Rename package in `pyproject.toml`: name to
    `fluid-postgres-mcp`, version to `0.1.0`, requires-python to
    `>=3.10`, entry point to `fluid-postgres-mcp`. Update author
    and project URLs. [verify: code-only]
  - [X] 1.3 Remove `AccessMode` enum, `current_access_mode`
    global, `get_sql_driver()` function, and `--access-mode`
    CLI argument from `server.py`. Replace
    `get_sql_driver()` calls with direct `SqlDriver(conn=
    db_connection)`. Remove conditional tool registration —
    always register `execute_sql` as unrestricted.
    (`server.py:49-68,607-624`) [verify: auto-test]
  - [X] 1.4 Strip `SafeSqlDriver`: remove `_validate()`,
    `_validate_node()`, `execute_query()` override, timeout
    logic, `ALLOWED_*` class vars. Keep static methods:
    `execute_param_query()`, `param_sql_to_query()`,
    `sql_to_query()`. Keep pglast imports only in
    `bind_params.py`, `dta_calc.py`, `index_opt_base.py`,
    `llm_opt.py`. (`safe_sql.py`) [verify: auto-test]
  - [X] 1.5 Update `.gitignore`: add `refs/`, `*.egg-info/`,
    `dist/`, `.venv*/`, `__pycache__/`, `.pytest_cache/`.
    [verify: code-only]
  - [X] 1.6 Verify baseline: `pip install -e .` succeeds,
    `pytest tests/unit/` passes (existing upstream unit
    tests). Fix any import errors from the stripping.
    [verify: auto-test]
    → pytest: 101 passed, 24 skipped, 1 xfailed [live]
      (2026-05-08). Fixed: typing.override→typing_extensions,
      multiline f-string syntax (3.12→3.11), removed
      AccessMode/SafeSqlDriver validation tests, patched
      test_server_integration to use SqlDriver instead of
      get_sql_driver, added hatch wheel packages config.

- [X] 2.0 **User Story:** As a developer, I want a configuration
  module so that all server settings are parsed from CLI args
  and environment variables in one place [4/4]
    → pytest: 110 passed (9 config + 101 baseline) [live]
      (2026-05-08)
  - [X] 2.1 Create `src/postgres_mcp/config.py` with
    dataclasses: `ReconnectConfig` (initial_delay,
    max_delay, max_attempts, pre_connect_script,
    hook_timeout) and `ServerConfig` (default_timeout_ms,
    output_dir, event_buffer_size, reconnect:
    ReconnectConfig). All fields with defaults per tech
    design C7 table. [verify: code-only]
  - [X] 2.2 Write tests for config parsing: env var override
    of defaults, CLI arg override of env vars, zero/None
    handling for optional fields.
    (`tests/unit/test_config.py`) [verify: auto-test]
  - [X] 2.3 Add `parse_config(args, env)` function that
    merges argparse namespace + os.environ into
    `ServerConfig`. Env var names prefixed `PGMCP_`.
    [verify: auto-test]
  - [X] 2.4 Add new CLI arguments to `server.py` argparse:
    `--default-timeout`, `--reconnect-initial-delay`,
    `--reconnect-max-delay`, `--reconnect-max-attempts`,
    `--pre-connect-script`, `--hook-timeout`,
    `--event-buffer-size`, `--output-dir`. Remove
    `--access-mode`. Wire `parse_config()` into `main()`.
    [verify: auto-test]

- [X] 3.0 **User Story:** As an AI agent, I want the MCP server
  to automatically reconnect when the database connection drops
  so that my session survives transient failures (C1) [8/8]
    → pytest: 20 reconnect tests passed [live] (2026-05-08)
  - [X] 3.1 Add `ConnState` enum to `sql_driver.py`:
    `DISCONNECTED`, `CONNECTED`, `QUERYING`, `RECONNECTING`,
    `ERROR`. Add `state` property and `reconnect_count`
    counter to `DbConnPool`. [verify: code-only]
  - [X] 3.2 Write tests for reconnection logic: successful
    reconnect after pool failure, exponential backoff
    timing, max_attempts exhaustion, state transitions
    through the state machine.
    (`tests/unit/sql/test_reconnect.py`) [verify: auto-test]
  - [X] 3.3 Implement `_reconnect_loop()` in `DbConnPool`:
    close old pool, create new pool with exponential
    backoff `min(initial_delay * 2^attempt, max_delay)`.
    Log each attempt. Transition states per state machine
    in tech design. [verify: auto-test]
  - [X] 3.4 Write tests for connection error detection:
    `psycopg.OperationalError` during `execute_query()`
    triggers reconnect on next call, connection marked
    invalid, state transitions to RECONNECTING.
    [verify: auto-test]
  - [X] 3.5 Modify `SqlDriver.execute_query()` to catch
    `psycopg.OperationalError`, mark pool invalid, and
    raise a clear error. On next call, if pool invalid,
    trigger `_reconnect_loop()` before executing.
    [verify: auto-test]
  - [X] 3.6 Write tests: server process never crashes on
    connection failure — `DbConnPool` catches all
    exceptions during reconnect loop, logs them, returns
    error to caller. [verify: auto-test]
  - [X] 3.7 Implement crash protection: wrap reconnect loop
    in try/except that catches all exceptions, logs via
    `obfuscate_password()`, transitions to ERROR state.
    ERROR state allows retry on next query attempt.
    [verify: auto-test]
  - [X] 3.8 Accept `ReconnectConfig` in `DbConnPool.__init__`
    and wire it from `ServerConfig` in `server.py:main()`.
    [verify: auto-test]

- [X] 4.0 **User Story:** As a user, I want to run a pre-connect
  script before each connection attempt so that network tunnels
  or VPNs are established automatically (C1) [4/4]
    → pytest: 10 hook tests passed [live] (2026-05-08)
  - [X] 4.1 Write tests for pre-connect hook: script called
    before connect, exit 0 proceeds, non-zero skips
    connect and continues backoff, timeout kills script,
    no script configured is a no-op.
    (`tests/unit/sql/test_pre_connect_hook.py`)
    [verify: auto-test]
  - [X] 4.2 Implement `_run_pre_connect_hook()` in
    `DbConnPool`: use `asyncio.create_subprocess_exec`,
    capture stdout/stderr, log at DEBUG, respect
    `hook_timeout`. Return bool success.
    [verify: auto-test]
  - [X] 4.3 Integrate hook into `_reconnect_loop()` and
    initial `pool_connect()`: call before each connection
    attempt. On hook failure, log error and continue
    backoff without attempting connect. [verify: auto-test]
  - [X] 4.4 Write test: hook with PATH lookup (executable
    name without path) and absolute path both work.
    [verify: auto-test]

- [X] 5.0 **User Story:** As an AI agent, I want per-query
  timeouts via SET LOCAL statement_timeout so that runaway
  queries don't block my session (C2) [4/4]
    → pytest: 6 timeout tests passed [live] (2026-05-08)
  - [X] 5.1 Write tests for timeout behavior: query with
    timeout_ms wraps in BEGIN + SET LOCAL + COMMIT/ROLLBACK,
    `QueryCanceled` exception returns clear error message,
    connection usable after timeout, timeout_ms=0 means no
    timeout, server default applied when no per-query value.
    (`tests/unit/sql/test_timeout.py`) [verify: auto-test]
  - [X] 5.2 Add `timeout_ms` parameter to
    `SqlDriver.execute_query()`. When set (or server
    default applies), execute within transaction:
    `BEGIN; SET LOCAL statement_timeout = '{ms}';
    <query>; COMMIT`. Catch `psycopg.errors.QueryCanceled`,
    ROLLBACK, return descriptive error. (`sql_driver.py`)
    [verify: auto-test]
  - [X] 5.3 Add `timeout_ms` parameter to `execute_sql` tool
    in `server.py`. Pass server default from `ServerConfig`
    when per-query value not provided. [verify: auto-test]
  - [X] 5.4 Write test: timeout on `execute_to_file()` also
    works (COPY with SET LOCAL in same transaction).
    [verify: auto-test]
    → 2 tests in TestTimeoutOnFileOutput passed [live]
      (2026-05-08)

- [X] 6.0 **User Story:** As an AI agent, I want to stream query
  results to a CSV file so that I can handle result sets of
  any size without exhausting memory (C2) [8/8]
    → pytest: 11 file output tests passed [live] (2026-05-08)
  - [X] 6.1 Write tests for `execute_to_file()`: file created
    with CSV header, data written block-by-block, row count
    from `statusmessage`, byte count from accumulated
    `len(data)`, empty result produces header-only file.
    (`tests/unit/sql/test_file_output.py`)
    [verify: auto-test]
  - [X] 6.2 Implement `SqlDriver.execute_to_file(query,
    file_path, timeout_ms, on_progress)` using psycopg3
    `async with cur.copy("COPY ({query}) TO STDOUT WITH
    CSV HEADER")`. Write blocks to file via `async for
    data in copy`. Parse final row count from
    `cur.statusmessage`. (`sql_driver.py`)
    [verify: auto-test]
  - [X] 6.3 Write tests for column name extraction: column
    names returned in metadata from first line of COPY
    output (CSV header row). [verify: auto-test]
  - [X] 6.4 Implement column extraction: parse first block
    for CSV header before writing. Return column list in
    result metadata. [verify: auto-test]
  - [X] 6.5 Add `output_file` and `output_mode` parameters
    to `execute_sql` tool in `server.py`. Route to
    `execute_to_file()` for `file` mode. Return metadata-
    only response: `{file, rows, bytes, columns}`.
    [verify: auto-test]
  - [X] 6.6 Write tests for `file+inline` mode: both file
    written and inline data returned. Query executes twice.
    [verify: auto-test]
  - [X] 6.7 Implement `file+inline` mode in `execute_sql`:
    call `execute_to_file()` then `execute_query()`.
    Return combined response with file metadata + inline
    rows. [verify: auto-test]
  - [X] 6.8 Write test: `output_dir` config respected —
    relative `output_file` resolved against `output_dir`.
    Absolute `output_file` used as-is. [verify: auto-test]

- [X] 7.0 **User Story:** As an AI agent, I want a status tool
  with ring-buffered event history so that I can diagnose
  connection issues and query performance (C3+C4) [6/6]
    → pytest: 8 event_store + 8 status_tool tests passed
      [live] (2026-05-08)
  - [X] 7.1 Write tests for EventStore: ring buffer wraps
    when full, per-category independence, `get_events(n)`
    returns most recent N, `record_query()` stores
    QueryRecord, configurable buffer sizes.
    (`tests/unit/test_event_store.py`) [verify: auto-test]
  - [X] 7.2 Implement `EventStore` in
    `src/postgres_mcp/event_store.py`: `EventCategory`
    enum, `Event` and `QueryRecord` dataclasses, ring
    buffer via `collections.deque(maxlen=N)`.
    [verify: auto-test]
  - [X] 7.3 Wire EventStore into DbConnPool and SqlDriver:
    record errors on connection failure, events on
    reconnect/state transitions, warnings on hook failures,
    query metadata on each execute. Instantiate EventStore
    as global alongside `db_connection` in `server.py`.
    [verify: auto-test]
  - [X] 7.4 Write tests for status tool: minimal response
    (state only), with errors/warnings/events params,
    with metadata (db name, version, reconnect count,
    uptime), with queries param. No SQL text in output.
    (`tests/unit/test_status_tool.py`) [verify: auto-test]
  - [X] 7.5 Implement `status` tool in `server.py`: register
    with FastMCP, query DbConnPool for state, query
    EventStore for history, cache db metadata on connect.
    [verify: auto-test]
  - [X] 7.6 Write test: status tool response never contains
    connection string fragments — verify
    `obfuscate_password` applied. [verify: auto-test]

- [X] 8.0 **User Story:** As an AI agent, I want progress
  notifications during long-running file exports so that I
  can relay status to the user (C6) [4/4]
    → pytest: 4 progress tests passed [live] (2026-05-08)
  - [X] 8.1 Write tests for progress callback: `on_progress`
    callable invoked with (rows_written, bytes_written,
    elapsed_seconds) during `execute_to_file()`. Verify
    called at reasonable intervals (not every block).
    (`tests/unit/sql/test_progress.py`)
    [verify: auto-test]
  - [X] 8.2 Add `on_progress` callback parameter to
    `execute_to_file()`. Call it every 100K rows or 10MB,
    whichever comes first. [verify: auto-test]
  - [X] 8.3 Wire progress in `execute_sql` tool: inject
    `ctx.report_progress(progress, total, message)` as
    the `on_progress` callback. Add `Context` type hint
    to `execute_sql` function signature for FastMCP
    injection. (`server.py`) [verify: auto-test]
  - [X] 8.4 Write test: progress notification not emitted
    for inline mode (small results). [verify: auto-test]

- [X] 9.0 **User Story:** As a developer, I want integration
  tests against a real PostgreSQL instance so that all
  features are verified end-to-end [6/6]
    → PostgreSQL deployed to k8s cluster (`pgmcp-test`
      namespace) via Bitnami Helm chart; ephemeral
      (install/uninstall per test run). 24 integration
      tests passing [live] (2026-05-09). Found and fixed
      2 bugs: memoryview handling in COPY, credential
      leak in status tool events.
      NOTE: these tests exercise the library layer
      (DbConnPool/SqlDriver) against real PG, not the
      MCP server process. Server-level E2E in story 10.0.
  - [X] 9.1 Create `tests/k8s_fixtures.py` with Helm-based
    PostgreSQL lifecycle: `helm install` bitnami/postgresql
    into `pgmcp-test` namespace, `kubectl port-forward` for
    local access, pytest fixture that connects to k8s PG,
    creates test tables with `generate_series()` for large
    datasets. Teardown: `helm uninstall` + delete namespace.
    (`tests/k8s_fixtures.py`, `tests/conftest.py`)
    [verify: code-only]
  - [X] 9.2 Integration test: file export of 500K rows —
    CSV file valid, row count correct, memory bounded.
    (`tests/integration/test_file_output.py`)
    [verify: auto-test]
    → pytest: 4 passed in 81s [live] (2026-05-09). Fixed
      memoryview bug in _copy_to_file (psycopg3 yields
      memoryview, not bytes)
  - [X] 9.3 Integration test: `pg_terminate_backend()` drops
    connection, next query triggers reconnect, subsequent
    query succeeds. (`tests/integration/test_reconnect.py`)
    [verify: auto-test]
    → pytest: 4 passed in 88s [live] (2026-05-09). Verified
      reconnect after pool invalidation, count increments,
      data integrity, and pool resilience to single backend kill
  - [X] 9.4 Integration test: `pg_sleep(10)` with
    `timeout_ms=1000` returns timeout error in ~1s,
    next query succeeds on same connection.
    (`tests/integration/test_timeout.py`) [verify: auto-test]
    → pytest: 5 passed in 64s [live] (2026-05-09). Verified
      timeout cancellation, connection reuse after timeout,
      no-timeout and zero-timeout paths
  - [X] 9.5 Integration test: pre-connect hook script
    executes before connection. Use a script that writes
    a marker file; verify file exists after connect.
    (`tests/integration/test_pre_connect.py`)
    [verify: auto-test]
    → pytest: 4 passed in 51s [live] (2026-05-09). Verified
      marker file creation, counter increment on reconnect,
      failed hook blocks connect, no-hook noop
  - [X] 9.6 Integration test: status tool returns accurate
    state and history after a sequence of queries, a
    forced connection drop, and reconnection.
    (`tests/integration/test_status.py`) [verify: auto-test]
    → pytest: 7 passed in 64s [live] (2026-05-09). Fixed
      credential leak in status tool event output — added
      obfuscate_password() to all event messages. Verified
      full sequence: connect, query, terminate, reconnect,
      status reflects all history

- [X] 10.0 **User Story:** As a developer, I want E2E tests
  that boot the actual MCP server process and exercise it
  through the MCP protocol so that CLI args, config wiring,
  tool routing, and server lifecycle are verified [5/5]
    → 22 E2E tests passing via MCP stdio protocol [live]
      (2026-05-09). Found and fixed: execute_sql returning
      isError=False on errors, added __main__.py for
      python -m invocation, increased helm timeout to 300s
    → Uses k8s PG for the database backend. Server started
      as a subprocess, MCP Python SDK client connects via
      stdio transport.
  - [X] 10.1 Create `tests/e2e/mcp_client_fixtures.py` with
    a fixture that starts `fluid-postgres-mcp` as a subprocess
    via stdio transport, connects an MCP SDK client
    (`mcp.ClientSession`), lists tools, and tears down on
    completion. Requires k8s PG from `k8s_fixtures`.
    (`tests/e2e/mcp_client_fixtures.py`) [verify: code-only]
  - [X] 10.2 E2E test: CLI arg wiring — server started with
    `--pre-connect-script` (marker-file script), verify marker
    exists after server connects. Start with `--default-timeout
    1000`, run `SELECT pg_sleep(5)` via MCP, verify timeout
    error. Start with `--output-dir /tmp/test-out`, run file
    export, verify file created in that dir. Start with
    `--event-buffer-size 5`, generate >5 events, verify status
    tool only returns 5.
    (`tests/e2e/test_cli_wiring.py`) [verify: auto-test]
  - [X] 10.3 E2E test: call `execute_sql` tool through MCP
    client in all three output modes (inline, file,
    file+inline). Verify inline returns rows, file mode
    creates CSV with correct metadata response, file+inline
    returns both. Test with real data (generate_series).
    (`tests/e2e/test_mcp_execute_sql.py`) [verify: auto-test]
  - [X] 10.4 E2E test: call `status` tool through MCP client.
    Verify connected state, events after queries, metadata
    with reconnect count. Force connection drop via
    `pg_terminate_backend`, verify error/reconnect events
    appear in subsequent `status` call.
    (`tests/e2e/test_mcp_status.py`) [verify: auto-test]
  - [X] 10.5 E2E test: server lifecycle — start with invalid
    connection string (unreachable host), verify server stays
    alive and returns clear error on `execute_sql` call.
    Verify graceful shutdown: send SIGTERM, confirm process
    exits with code 0 within 5s.
    (`tests/e2e/test_server_lifecycle.py`) [verify: auto-test]

- [X] 11.0 **User Story:** As a developer, I want production-
  realistic disruption tests against the EC2/SSM infrastructure
  so that reconnection, pre-connect hooks, and status reporting
  are verified under real failure conditions [6/6]
    → 6 passed in 102s [live] (2026-05-09). All disruption
      scenarios verified: SSM tunnel, pg_terminate_backend,
      Docker container stop/start, Docker container restart.
      PG on EC2 runs in Docker — tests use docker compose
      commands via ssm:SendCommand.
    → Uses EC2 instance <EC2_INSTANCE_ID> (<EC2_REGION>),
      SSM tunneling, and SSM send-command for disruption.
      Server runs as MCP subprocess with a pre-connect script
      that establishes the SSM tunnel. Tests are destructive
      to the test PG instance but safe (dev environment).
  - [X] 11.1 Create `tests/e2e/ssm_fixtures.py` with helpers:
    AWS role assumption, SSM tunnel setup/teardown, SSM
    send-command wrapper for remote PG control, EC2 state
    management. Create a pre-connect script that opens an
    SSM tunnel to EC2:5432.
    (`tests/e2e/ssm_fixtures.py`) [verify: code-only]
  - [X] 11.2 E2E test: happy path — server boots with SSM
    tunnel pre-connect script, connects to EC2 PG, runs
    queries via MCP, `status` shows connected. Baseline
    for disruption tests.
    (`tests/e2e/test_ssm_disruption.py`) [verify: auto-test]
  - [X] 11.3 E2E test: tunnel kill — kill the SSM tunnel
    process mid-session. Next query fails. Pre-connect script
    re-establishes tunnel. Server reconnects. Subsequent
    queries succeed. `status` shows reconnect event.
    (`tests/e2e/test_ssm_disruption.py`) [verify: auto-test]
  - [X] 11.4 E2E test: connection kill via pg_terminate_backend
    through SSM tunnel. Kill all mcp_reader backends via SQL,
    verify error on next query, server reconnects, status
    shows connected state.
    (`tests/e2e/test_ssm_disruption.py`) [verify: auto-test]
  - [X] 11.5 E2E test: PG service stop/start — SSM
    send-command `docker compose stop/start postgres` on EC2.
    (`tests/e2e/test_ssm_disruption.py`) [verify: auto-test]
    → pytest: 1 passed in 77s [live] (2026-05-09). Container
      stopped, queries failed, container started, server
      reconnected, queries resumed
  - [X] 11.6 E2E test: PG service restart — SSM send-command
    `docker compose restart postgres`.
    (`tests/e2e/test_ssm_disruption.py`) [verify: auto-test]
    → pytest: 1 passed [live] (2026-05-09). Container
      restarted, server detected failure and reconnected
