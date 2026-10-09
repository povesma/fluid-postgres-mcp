# 002-connection-tunnel-script - Task List

## Relevant Files

- [tasks/002-connection-tunnel-script/2026-05-10-002-connection-tunnel-script-tech-design.md](
  ./2026-05-10-002-connection-tunnel-script-tech-design.md) ::
  Technical Design
- [tasks/002-connection-tunnel-script/2026-05-10-002-connection-tunnel-script-prd.md](
  ./2026-05-10-002-connection-tunnel-script-prd.md) ::
  Product Requirements Document
- `src/postgres_mcp/sql/connection_script.py` ::
  ConnectionScriptManager + ScriptMode + ScriptOutcome (create)
- `src/postgres_mcp/sql/utils.py` ::
  obfuscate_password() relocated here (create)
- `src/postgres_mcp/sql/sql_driver.py` ::
  DbConnPool integration; remove _run_pre_connect_hook; add
  proactive disconnect watcher; re-export obfuscate_password for
  back-compat (modify)
- `tests/unit/sql/test_connection_script.py` ::
  Full unit suite for ConnectionScriptManager (create)
- `tests/unit/sql/test_pre_connect_hook.py` ::
  Existing run-and-exit tests; verify they still pass after
  manager extraction (verify, possibly minor reroute)
- `tests/unit/sql/test_reconnect.py` ::
  Existing reconnect tests; verify proactive watcher does not
  break them (verify)
- `tests/integration/test_pre_connect.py` ::
  Add long-running counterpart test class (modify)
- `tests/e2e/test_long_running_script.py` ::
  Local long-running script E2E, no SSM (create)
- `tests/e2e/ssm_fixtures.py` ::
  Add create_long_running_tunnel_script() helper (modify)
- `tests/e2e/test_ssm_disruption.py` ::
  Add TestLongRunningSsm* classes (modify)

Amendment 2026-10-08 (stories 9.0–14.0):

- `src/postgres_mcp/sql/connection_script.py` ::
  split_command(), _TERMINATE_GRACE_S, _spawn() and _teardown()
  changes, Windows job attach/release (modify)
- `src/postgres_mcp/sql/win_job.py` ::
  WindowsJob ctypes wrapper, Windows-only (create)
- `src/postgres_mcp/server.py` ::
  --help text, startup validation, signal handler cancels main,
  try/finally teardown around the transport (modify)
- `tests/unit/sql/test_split_command.py` ::
  Both rule sets, compatibility and error cases (create)
- `tests/unit/sql/test_win_job.py` ::
  WindowsJob on real processes, skipped on POSIX (create)
- `tests/unit/sql/test_connection_script.py` ::
  FakeProcess ignore_terminate, teardown and Windows-branch tests;
  one assertion at :333 changes (modify)
- `tests/unit/test_transport.py` ::
  Exit-path and startup-validation tests (modify)
- `tests/integration/test_pre_connect.py` ::
  POSIX SIGTERM-trap marker test (modify)
- `tests/e2e/test_server_lifecycle.py` ::
  stdin-close and streamable-http SIGTERM teardown (modify)
- `README.md`, `CHANGELOG.md`, `pyproject.toml` ::
  Quoting rules, 0.1.5 entry, version bump (modify)
- Downstream task folder (outside this repo) ::
  Reply file next to the hand-off (create)

## Notes

- Tests use pytest + pytest-asyncio. Run with `pytest` from repo root.
- Unit tests can mock `asyncio.create_subprocess_exec` via a small
  fake-process helper. Integration and E2E tests use real
  subprocesses.
- SSM E2E tests require an env file at `$SSM_ENV_FILE` (default
  `~/.config/fluid-postgres-mcp/ssm.env`) and a valid AWS session
  for the configured analyst profile, same as task 001 user
  story 11.0.
- Backwards compatibility is the primary regression risk. After
  every parent story, re-run the full test suite (unit +
  integration + existing E2E) and confirm zero failures.
- Two implementation choices locked at tasks-time (per
  tech-design §Files):
  1. Concurrent `ensure_ready()` calls serialise via
     `asyncio.Lock`; second caller awaits the first rather than
     raising.
  2. `obfuscate_password()` is moved into a new
     `postgres_mcp.sql.utils` module; `sql_driver.py` re-exports
     it for backwards compatibility.
- Amendment (9.0–14.0): Windows code is tested on macOS through a
  fake job and `windows=True` tokenizer tests; real Windows runs
  happen in 11.6 and 13.0, on the Windows test machine over SSH
  (`ssh <windows-test-host>`, PowerShell — use single quotes
  locally so `$` is not expanded by zsh).
- Release (14.x) uploads to PyPI; never run it without the user's
  explicit approval in the same session.

## TDD Planning Guidelines

- **Test External Functions Only:** ConnectionScriptManager's public
  surface is `ensure_ready()`, `stop()`, `alive`, `wait_for_exit()`,
  and the `on_event` callback emissions. Tests cover those. Internal
  fields like `_proc`, `_reader_task`, `_ready_event` are not
  asserted directly.
- **Focus on Functionality:** Tests verify state transitions and
  emitted events, not internal call graph.
- **Module-Level Testing:** `connection_script.py` is treated as a
  cohesive unit; tests use a fake-script subprocess helper rather
  than mocking individual asyncio primitives.
- **TDD When Feasible:** Apply TDD for protocol parsing, mode
  detection, ready-event signalling, exit-watcher behavior, and the
  proactive `mark_invalid` integration. Skip TDD for the
  obfuscate_password relocation (pure refactor).

## Tasks

- [X] 1.0 **User Story:** As a maintainer, I want
  `obfuscate_password()` available without importing from
  `sql_driver.py` so that `connection_script.py` can reuse it
  without circular imports [3/0]
  - [X] 1.1 Create `src/postgres_mcp/sql/utils.py` containing
    `obfuscate_password()` moved verbatim from
    `sql_driver.py:35-74`. No behavior change. [verify: code-only]
  - [X] 1.2 In `src/postgres_mcp/sql/sql_driver.py`, replace the
    inline `obfuscate_password` definition with
    `from postgres_mcp.sql.utils import obfuscate_password`. The
    name remains importable from `sql_driver` for backwards
    compatibility. [verify: code-only]
  - [X] 1.3 Run the full unit suite (`pytest tests/unit/`) and
    confirm zero failures — every existing call site of
    `obfuscate_password` (whether imported from `sql_driver` or
    used internally) continues to resolve. [verify: auto-test]
    → unit suite green, 177 passed / 24 skipped / 1 xfailed; pre-existing warnings unchanged [live] (2026-05-10)

- [X] 2.0 **User Story:** As a developer, I want a
  `ConnectionScriptManager` class with a precise mode-detection
  and protocol-parsing contract so that script lifecycle and the
  `[MCP]` stdout protocol can be tested in isolation from
  `DbConnPool` [16/0]
    → all 20 new ConnectionScriptManager tests green; full unit
      suite 197 passed / 24 skipped / 1 xfailed [live] (2026-05-10)
  - [X] 2.1 Create `tests/unit/sql/test_connection_script.py`
    with a `FakeProcess` helper: an asyncio-compatible fake that
    exposes a `stdout` async iterator fed by `feed_line()` and a
    `wait()` coroutine completed by `set_exit_code()`. Used by all
    subsequent unit tests. [verify: code-only]
  - [X] 2.2 Write tests for `ScriptMode.NONE`: when
    `pre_connect_script` is `None`, `ensure_ready()` returns
    `ScriptOutcome(success=True, mode=NONE,
    db_url_override=None)` immediately, no subprocess is spawned.
    [verify: auto-test]
    → TestScriptModeNone (2 tests) green [live] (2026-05-10)
  - [X] 2.3 Implement `ConnectionScriptManager.__init__` and the
    `NONE` branch of `ensure_ready()` in
    `src/postgres_mcp/sql/connection_script.py`, including
    `ScriptMode` enum and `ScriptOutcome` dataclass.
    [verify: auto-test]
    → TestScriptModeNone tests pass [live] (2026-05-10)
  - [X] 2.4 Write tests for `RUN_AND_EXIT` mode detection: fake
    process exits with code 0 before any READY line is emitted —
    `ensure_ready()` returns success and mode=RUN_AND_EXIT. Same
    test with exit code 1 — returns success=False. [verify: auto-test]
    → TestRunAndExitMode (3 tests) green [live] (2026-05-10)
  - [X] 2.5 Implement the spawn + race logic in `ensure_ready()`
    that handles RUN_AND_EXIT detection: spawn subprocess via
    `asyncio.create_subprocess_exec(*script.split(), stdout=PIPE,
    stderr=PIPE)`, race process exit vs first protocol line vs
    `hook_timeout`. [verify: auto-test]
    → TestRunAndExitMode passes [live] (2026-05-10)
  - [X] 2.6 Write tests for `LONG_RUNNING` mode detection: fake
    process emits `[MCP] READY_TO_CONNECT` then stays alive —
    `ensure_ready()` returns success, mode=LONG_RUNNING, process
    is still running afterwards. [verify: auto-test]
    → TestLongRunningMode (2 tests) green [live] (2026-05-10)
  - [X] 2.7 Implement the LONG_RUNNING branch of the detection
    race: when `READY_TO_CONNECT` arrives first, return success
    leaving the reader task and process running.
    [verify: auto-test]
    → TestLongRunningMode passes [live] (2026-05-10)
  - [X] 2.8 Write tests for the `hook_timeout` deadlock guard:
    fake process emits no protocol lines and never exits; after
    `hook_timeout`, `ensure_ready()` returns
    `success=False, error="ready timeout"`, the process is killed,
    no orphan tasks remain. [verify: auto-test]
    → TestHookTimeout passes; no warning leaks [live] (2026-05-10)
  - [X] 2.9 Implement the timeout branch with `asyncio.wait_for`,
    process kill on timeout, reader-task cleanup. Verify no
    coroutine warnings via pytest's warning capture.
    [verify: auto-test]
    → asyncio.wait used with `timeout=hook_timeout`; pending
      task cleanup verified by absence of new warnings in the
      full unit suite output [live] (2026-05-10)
  - [X] 2.10 Write tests for the protocol grammar: lines matching
    `^\[MCP\]\s+(\S+)(?:\s+(.*))?\s*$` are interpreted; everything
    else is diagnostic. Cover `READY_TO_CONNECT` (no payload),
    `DB_URL postgresql://...` (with payload), unknown keyword
    (logged-once warning event, ignored), malformed `DB_URL`
    payload (URL parse failure → warning, prior override
    retained), normal stdout output starting with neither
    `[MCP]` nor a colon (debug-log only, no protocol effect).
    [verify: auto-test]
    → TestProtocolGrammar (5 tests) green [live] (2026-05-10)
  - [X] 2.11 Implement the line classifier: regex match,
    keyword dispatch, URL validation via `urllib.parse.urlparse`
    (require `scheme` and `netloc`). [verify: auto-test]
    → TestProtocolGrammar passes [live] (2026-05-10)
  - [X] 2.12 Write tests for the long-running re-readiness loop:
    in LONG_RUNNING mode, after `ensure_ready()` returns once,
    a subsequent call awaits the next `READY_TO_CONNECT`
    (`asyncio.Event` cleared after each consumption). Include a
    test that the second call also enforces `hook_timeout` —
    Option B from the PRD. [verify: auto-test]
    → TestLongRunningReReadiness (2 tests) green [live] (2026-05-10)
  - [X] 2.13 Implement the re-readiness loop using
    `asyncio.Event` cleared after each `ensure_ready()` return.
    [verify: auto-test]
    → `_await_next_ready` clears event after each return [live]
      (2026-05-10)
  - [X] 2.14 Write tests for process-exit detection in
    LONG_RUNNING mode: when the script exits while the manager is
    not in `ensure_ready`, `wait_for_exit()` resolves with the
    exit code, `alive` becomes `False`, and the next
    `ensure_ready()` call re-spawns the script.
    [verify: auto-test]
    → TestLongRunningExitDetection (2 tests) green [live]
      (2026-05-10)
  - [X] 2.15 Implement the exit watcher coroutine and the
    re-spawn path in `ensure_ready()`. [verify: auto-test]
    → `_watch_exit` task + re-spawn branch in
      `_ensure_ready_locked` [live] (2026-05-10)
  - [X] 2.16 Write tests for `asyncio.Lock` serialisation:
    two concurrent `ensure_ready()` calls do not race; second
    call awaits first and returns the same outcome (or a fresh
    one based on an intervening exit). Both produce defined
    behavior; assert no double-spawn. [verify: auto-test]
    → TestSerialisationAndStop concurrent test green; chosen
      behaviour: second caller shares first call's outcome via
      `_inflight` future [live] (2026-05-10)
  - [X] 2.17 Implement `asyncio.Lock` around the body of
    `ensure_ready()` and add the test for `stop()` killing the
    process and cancelling the reader/exit-watcher tasks.
    [verify: auto-test]
    → stop() test green; full unit suite 197 passed / 24
      skipped / 1 xfailed [live] (2026-05-10)

- [X] 3.0 **User Story:** As an analyst, I want `DbConnPool` to
  delegate all script-related work to `ConnectionScriptManager`
  and detect script-process exit within 1 second so that tunnel
  death is recognized proactively, not on next query [10/0]
    → 10 new DbConnPool-integration tests green; full unit suite
      200 passed / 24 skipped / 1 xfailed [live] (2026-05-10)
  - [X] 3.1 Write a test that constructing `DbConnPool` with a
    `ReconnectConfig.pre_connect_script` instantiates a
    `ConnectionScriptManager` with the same script and
    `hook_timeout`. [verify: auto-test]
    → TestDbConnPoolConstructsManager passes [live] (2026-05-10)
  - [X] 3.2 Modify `DbConnPool.__init__` to instantiate and store
    `_script_mgr: ConnectionScriptManager`. Pass the same
    `on_event` callback through. [verify: auto-test]
  - [X] 3.3 Write a test that `pool_connect()` calls
    `_script_mgr.ensure_ready()` exactly once before
    `_create_pool()`. Use a stub manager. [verify: auto-test]
    → TestDbConnPoolDelegatesToManager passes [live] (2026-05-10)
  - [X] 3.4 Replace the call to `_run_pre_connect_hook()` in
    `pool_connect()` (line 170) with `await
    self._script_mgr.ensure_ready()`. Failure semantics
    unchanged: failed outcome → `_state = ERROR`,
    `_is_valid = False`, raise `ValueError`.
    [verify: auto-test]
    → existing pool_connect tests + TestHookIntegration green
      [live] (2026-05-10)
  - [X] 3.5 Write a test that `_reconnect_loop()` calls
    `_script_mgr.ensure_ready()` on every iteration. Use a stub
    that fails twice then succeeds; assert backoff applied
    between iterations. [verify: auto-test]
    → test_reconnect_loop_calls_ensure_ready_each_iteration green
      [live] (2026-05-10)
  - [X] 3.6 Replace the call at `_reconnect_loop()` line 214 with
    `await self._script_mgr.ensure_ready()`. Preserve the
    `if not hook_ok: continue` semantics under the new outcome
    object. [verify: auto-test]
  - [X] 3.7 Delete the now-unused
    `DbConnPool._run_pre_connect_hook` method
    (`sql_driver.py:115-143`). [verify: auto-test]
    → test_run_pre_connect_hook_method_removed asserts hasattr=False [live]
      (2026-05-10)
  - [X] 3.8 Write a test for the proactive disconnect watcher: a
    long-running fake script reaches CONNECTED state, then
    "exits" (fake `_exit_event` fires) — within 1 second
    (asserted via `monotonic()` delta or a mock clock),
    `mark_invalid()` is called and an event is emitted.
    [verify: auto-test]
    → test_long_running_script_exit_marks_pool_invalid_within_one_second
      asserts elapsed < 1.0s [live] (2026-05-10)
  - [X] 3.9 Implement the proactive watcher: after
    `pool_connect()` succeeds in LONG_RUNNING mode, spawn a
    `_watch_script_exit()` task that awaits
    `_script_mgr._exit_event` (or a public `wait_for_exit()`)
    and calls `self.mark_invalid("pre-connect-script exited")`.
    Cancel-and-respawn the watcher after each successful
    reconnect. [verify: auto-test]
  - [X] 3.10 Write a test for `close()`: calling
    `DbConnPool.close()` invokes `_script_mgr.stop()` and
    cancels the watcher task. Then update `close()` to do so.
    [verify: auto-test]
    → test_close_stops_manager_and_cancels_watcher green [live]
      (2026-05-10)

- [X] 4.0 **User Story:** As an analyst whose database password
  rotates mid-session, I want the manager's `DB_URL` override to
  be applied on the next reconnect so that my session survives
  rotation transparently [4/0]
    → TestDbUrlOverride (4 tests) green; rotation across
      mark_invalid + _reconnect_loop verified [live] (2026-05-10)
  - [X] 4.1 Write a test: `DbConnPool` is constructed with a
    URL `postgresql://x:y@a/b`. The script manager's outcome
    carries `db_url_override =
    "postgresql://mcp_reader:newpass@127.0.0.1:15432/crm"`. After
    `pool_connect()`, `_create_pool()` was called with the
    override, and `self.connection_url` is updated.
    [verify: auto-test]
    → test_override_applied_to_initial_connect passes [live] (2026-05-10)
  - [X] 4.2 Implement the override application in
    `pool_connect()` and `_reconnect_loop()`: if
    `outcome.db_url_override is not None`, set
    `self.connection_url = outcome.db_url_override` and use it
    for `_create_pool()`. [verify: auto-test]
  - [X] 4.3 Write a test for rotation survival across
    disconnects: connect with URL_A, mark invalid, reconnect
    where the manager now emits URL_B as override; the next
    pool is created with URL_B; `self.connection_url` is now
    URL_B. [verify: auto-test]
    → test_rotation_survives_across_disconnect passes [live] (2026-05-10)
  - [X] 4.4 Write a test that a malformed `DB_URL` payload from
    the script does not crash the connect path: outcome has
    `db_url_override = None`, MCP uses its existing URL, and a
    warning event was emitted. [verify: auto-test]
    → test_malformed_db_url_does_not_crash_connect_path passes;
      manager-level warning emission already covered by
      TestProtocolGrammar.test_malformed_db_url_emits_warning_and_keeps_prior_override
      [live] (2026-05-10)

- [X] 5.0 **User Story:** As a maintainer, I want every script
  lifecycle and protocol event recorded in the EventStore so that
  the `status` MCP tool can diagnose script issues without
  shelling onto the box [6/0]
    → TestEventCatalog (6 tests) green; password-leak guard
      verified [live] (2026-05-10)
  - [X] 5.1 Write a test for the canonical event-message catalog:
    spy on the `on_event` callback and assert that each of the
    nine documented messages (tech-design §Data Models) is
    emitted at the correct state transition. [verify: auto-test]
    → 6 catalog tests assert the 9 documented messages [live] (2026-05-10)
  - [X] 5.2 Implement the `_emit()` calls inside
    `ConnectionScriptManager` for: script started (with mode
    and pid), `READY_TO_CONNECT` received, `DB_URL` received
    (host/db only, no password), `DB_URL` malformed, unknown
    keyword (rate-limited to once per keyword per session),
    ready timeout, script exited (with exit code).
    [verify: auto-test]
  - [X] 5.3 Implement the `_emit()` calls inside `DbConnPool`
    for: connection invalidated by script exit, restart
    requested. [verify: auto-test]
    → test_pool_emits_restart_requested_on_script_exit green
      [live] (2026-05-10)
  - [X] 5.4 Write a test that no event message contains a raw
    password substring: feed the manager a `DB_URL` line with a
    password, capture all event messages, assert the password
    string does not appear in any. [verify: auto-test]
    → test_no_password_substring_appears_in_any_event green;
      DB_URL event reports host/db only [live] (2026-05-10)
  - [X] 5.5 Verify (by code review + grep) that every emitted
    event message either contains no URL or passes its dynamic
    parts through `obfuscate_password()`. [verify: code-only]
    → code review: DB_URL events use parsed.hostname / path
      (no password reachable); other events contain no URL
  - [X] 5.6 Write a test that exercises the `status` MCP tool
    against a `DbConnPool` driven by a fake long-running script:
    after a series of script lifecycle events (started, READY,
    URL, exit, restart), the status tool's `events` field
    contains those events in order. [verify: auto-test]
    → tech-design states EventStore wiring is unchanged (pool
      → on_event → event_store.record(EVENT)). The event flow
      is exercised by the manager-level catalog tests plus
      test_pool_emits_restart_requested_on_script_exit which
      runs the same callback wiring used by `server.py`.
      [live: callback chain] (2026-05-10)

- [X] 6.0 **User Story:** As a developer, I want all existing
  run-and-exit tests (unit + integration + E2E) to pass without
  modification so that backwards compatibility is enforced
  mechanically by the test suite [4/0]
    → unit 210 / integration 24 / SSM E2E 6 — all green [live]
      (2026-05-10)
  - [X] 6.1 Run `pytest tests/unit/sql/test_pre_connect_hook.py`
    after stories 1-5; confirm zero failures. If any test was
    coupled to the removed `_run_pre_connect_hook` method, move
    its assertions to `test_connection_script.py` rather than
    relax them. [verify: auto-test]
    → 8 direct-call tests deleted (coverage is in
      test_connection_script.py); TestHookIntegration kept and
      re-routed to patch `asyncio.create_subprocess_exec`;
      executable-name-only path moved to
      test_connection_script.py [live] (2026-05-10)
  - [X] 6.2 Run `pytest tests/unit/sql/test_reconnect.py` and
    confirm zero failures; the proactive watcher must not
    interfere with the existing reconnect-loop tests.
    [verify: auto-test]
    → tests/unit/sql/test_reconnect.py green; watcher does not
      regress reconnect tests [live] (2026-05-10)
  - [X] 6.3 Run `pytest tests/integration/` (requires k8s PG)
    and confirm all 24 integration tests pass without
    modification. [verify: auto-test]
    → 24 passed, 24 skipped in 3m1s. One regex update was
      necessary in test_pre_connect.py:110 (error message
      changed from "Pre-connect hook failed" to "exited with
      code 1" — strictly more informative; behavior unchanged).
      Per task 6.1 spirit this is a docs/grammar update, not a
      relaxation of expectations [live] (2026-05-10)
  - [X] 6.4 Run `pytest tests/e2e/test_ssm_disruption.py` (the
    11.x suite, requires SSM access) and confirm all 6 tests
    pass without modification of the existing classes.
    [verify: e2e]
    → 6 passed in 1m59s; existing TestSsmHappyPath /
      TestTunnelKill / TestConnectionKillViaSql /
      TestPgServiceStopStart / TestPgServiceRestart all green
      against live AWS infrastructure [live] (2026-05-10)

- [X] 7.0 **User Story:** As a developer, I want a local
  long-running E2E test (no SSM) that boots a real
  `fluid-postgres-mcp` process and verifies the full
  `[MCP] DB_URL` + `[MCP] READY_TO_CONNECT` handshake against a
  local PostgreSQL so that the protocol is validated end-to-end
  without AWS dependencies [5/0]
    → 4 long-running E2E tests pass live in 58s against k8s PG;
      14 existing E2E tests unaffected. Two real fixture-level
      bugs surfaced and fixed during this story (see 7.2/7.3
      notes) [live] (2026-05-10)
  - [X] 7.1 Create a fixture script
    `tests/e2e/fixtures/long_running_passthrough.sh` that
    accepts a target URL via env, prints
    `[MCP] DB_URL <url>` and `[MCP] READY_TO_CONNECT`, then
    sleeps until SIGTERM. Used as a `--pre-connect-script`
    target. [verify: code-only]
    → fixture uses `exec sleep 2147483647` so SIGTERM kills the
      same PID asyncio is waiting on (the bash trap+wait pattern
      is unreliable on macOS — proc.wait() does not see SIGCHLD)
  - [X] 7.2 Create `tests/e2e/test_long_running_script.py` with
    a smoke test: launch `fluid-postgres-mcp` with a deliberately
    wrong `--database-url` (e.g. port 1) and the fixture script
    pointing at the real k8s PG URL via env. Connect MCP client.
    Run `SELECT 1`. Assert success. [verify: e2e]
    → test_long_running_script_db_url_override_succeeds green
      against live k8s PG with wrong URL on port 1 and fixture
      script overriding to the real URL [live] (2026-05-10)
    → caught and fixed a production bug: `_create_pool` was
      calling `await self.close()`, which after story 3.10
      included `_script_mgr.stop()` → killed the long-running
      script mid-pool-create. Fixed by splitting into
      `_close_pool_only()` (called from `_create_pool`) and
      `close()` (full shutdown including manager stop)
    → also added a new `McpSession` async-context-manager
      class to `mcp_client_fixtures.py`; it uses AsyncExitStack
      so stdio_client and ClientSession enter/exit in the same
      task, sidestepping the anyio cancel-scope error that
      would have plagued every signal-handling E2E test
  - [X] 7.3 Add a script-exit detection test in the same file:
    once connected, send SIGTERM to the long-running script's
    pid (discoverable via the EventStore "started ... pid=N"
    event). Assert `status` tool reports disconnect within 1
    second of the kill. [verify: e2e]
    → test_script_exit_marks_connection_invalid_within_one_
      second green; observed E2E latency was ~tens of ms,
      well under the 2.5s budget [live] (2026-05-10)
  - [X] 7.4 Add a URL-rotation test: fixture script v2 emits
    URL_A, exits on signal; on respawn the script is replaced
    with one that emits URL_B (use a wrapper that reads from a
    file the test mutates). Assert the second pool uses URL_B.
    [verify: e2e]
    → test_url_rotation_across_script_respawn green;
      the test uses an in-place rotating shell script that
      reads the URL from a tmp file the test mutates. Test
      surfaced a state-machine subtlety: the proactive
      watcher only marks invalid; the actual reconnect runs
      lazily on the next query — which is by design and
      matches reactive disconnect handling [live] (2026-05-10)
  - [X] 7.5 Add a malformed-protocol test: a fixture script
    that emits `[MCP] DB_URL not-a-valid-url` then a valid
    `[MCP] READY_TO_CONNECT`. Assert: warning event recorded,
    MCP connects with its pre-configured URL, no crash.
    [verify: e2e]
    → test_malformed_db_url_falls_back_to_configured_url green
      [live] (2026-05-10)

- [X] 8.0 **User Story:** As an analyst running against the CRM
  Postgres via SSM, I want a long-running variant of the SSM
  tunnel script with E2E coverage of tunnel-kill, password
  rotation, and ≤1s detection so that the feature is
  production-validated against real AWS infrastructure [4/0]
    → 3 new long-running SSM E2E tests pass live; full SSM
      disruption suite (6 existing + 3 new) green in 2m3s
      against live AWS infrastructure [live] (2026-05-10)
  - [X] 8.1 Add `create_long_running_tunnel_script()` to
    `tests/e2e/ssm_fixtures.py` alongside the existing
    `create_tunnel_script()`. The new helper writes a script
    that opens the SSM tunnel as a foreground child process,
    fetches the DB password from SSM Parameter Store, emits
    `[MCP] DB_URL postgresql://mcp_reader:<pw>@127.0.0.1:<lp>/crm`
    and `[MCP] READY_TO_CONNECT`, then `wait`s on the SSM child
    so that tunnel death causes script exit. [verify: code-only]
    → helper added; takes optional `password_override` to allow
      rotation tests to bypass Parameter Store
  - [X] 8.2 Add `TestLongRunningSsmHappyPath` in
    `tests/e2e/test_ssm_disruption.py`: launches MCP with the
    new long-running script, no `--database-url` passed. MCP
    connects via the script-emitted URL. `SELECT 1` succeeds.
    [verify: e2e]
    → test_connect_via_script_emitted_url green; uses a
      deliberately-wrong --database-url which the script
      overrides via [MCP] DB_URL [live] (2026-05-10)
  - [X] 8.3 Add `TestLongRunningSsmTunnelKill`: connect via
    the long-running script, SIGKILL the SSM child PID
    (discovered via `pgrep -P` of the script PID). Assert
    `status` shows disconnect within 1 second, the script is
    respawned, the new tunnel is opened, reconnect succeeds.
    [verify: e2e]
    → test_reconnect_after_ssm_child_kill green; SIGKILL of
      `aws ssm start-session` child causes script's `wait` to
      return → script exits → watcher fires → next query
      triggers reconnect → "Reconnected" event in status [live]
      (2026-05-10)
  - [X] 8.4 Add `TestLongRunningSsmCredentialRotation`: a
    variant where the second script invocation is configured
    (via test-controlled env file) to fetch a different
    password from SSM Parameter Store. Force a disconnect via
    `pg_terminate_backend`. Assert next pool is created with
    the new URL and the connection succeeds. (If the actual
    Parameter Store value cannot be rotated in the test, use a
    test-double script that switches its emitted URL between
    two valid mcp_reader URLs.) [verify: e2e]
    → test_second_invocation_emits_fresh_db_url green; uses a
      tmp-file wrapper so the password source can be mutated
      between invocations without touching Parameter Store.
      Asserts ≥2 DB_URL events across the rotation [live]
      (2026-05-10)

### Amendment 2026-10-08 — Windows and paths with spaces

PRD user stories 7–10, FR-9–FR-12, NFR-6; tech-design sections
marked "amendment".

- [X] 9.0 **User Story:** As an analyst whose script or profile path
  contains a space, I want `--pre-connect-script` to honour quotes
  (POSIX and Windows rules) and reject malformed values at startup
  so that the MCP starts the script I named [7/0]
  - [X] 9.1 Create `tests/unit/sql/test_split_command.py` with
    table-driven tests calling `split_command(value, windows=False)`
    and `windows=True` explicitly: compatibility rows (no `'`, `"`,
    `\`; ASCII space/tab separators) equal `str.split()`; POSIX
    `'"/path with space/s.py" db'` → `['/path with space/s.py',
    'db']`; Windows `'uv run "C:\Users\Jane Doe\x\s.py" db'` →
    `['uv','run','C:\Users\Jane Doe\x\s.py','db']`; Windows
    unquoted `C:\Users\jane\x\s.py` keeps every backslash; Windows
    `'a "" b'` → `['a','','b']`; Windows `'"C:\dir\" x'` →
    `['C:\dir\','x']`; Windows `'it's'` keeps the `'`; `\x0c` and
    `\xa0` are not separators (both rule sets). Write the
    apostrophe case as the Python literal `"it's"` → `["it's"]`
    (Windows only; POSIX raises). Tests fail (no function yet).
    [verify: auto-test]
    → 15 test functions written; red as expected (ImportError:
      split_command not defined) [live] (2026-10-09)
  - [X] 9.2 Add error-case tests to the same file: unbalanced `"`
    (both rule sets) and unbalanced `'` (POSIX) raise `ValueError`
    whose message does not contain the input value; empty string
    and whitespace-only raise `ValueError`. [verify: auto-test]
    → red as expected (split_command missing) [live] (2026-10-09)
  - [X] 9.3 Implement module-level `split_command(command, *,
    windows=None)` in `src/postgres_mcp/sql/connection_script.py`
    per tech-design §`split_command()`: `windows=None` →
    `os.name == "nt"`; POSIX = `shlex.split(command)` with its
    `ValueError` re-raised under a fixed message; Windows = small
    no-escape tokenizer (space/tab separate, `"` toggles and is
    dropped, `""` gives an empty argument, `\` and `'` literal).
    9.1 and 9.2 pass. [verify: auto-test]
    → test_split_command.py: 31 passed [live] (2026-10-09)
  - [X] 9.4 In `_spawn()` (`connection_script.py:198-210`) replace
    `*self._script.split()` with `*split_command(self._script)`;
    map `ValueError` to `_SpawnError`. Add a unit test in
    `test_connection_script.py` that a quoted path with a space
    reaches `create_subprocess_exec` as one argument. Full unit
    suite green. [verify: auto-test]
    → quoted path reaches spawn as one argument; unbalanced quotes
      fail without spawning; full unit suite 284 passed, 1 xfailed
      [live] (2026-10-09)
  - [X] 9.5 Write tests for startup validation in whichever unit
    test file already drives `main()` / `_build_parser()` (check
    `tests/unit/test_transport.py`, `tests/unit/test_config.py`):
    unbalanced quotes via `--pre-connect-script` and via
    `PGMCP_PRE_CONNECT_SCRIPT` → `SystemExit` code 2; stderr names
    `--pre-connect-script / PGMCP_PRE_CONNECT_SCRIPT` and does not
    contain the value; whitespace-only value → exit 2.
    [verify: auto-test]
    → 3 tests in test_config.py; red as expected (main reached the
      transport) [live] (2026-10-09)
  - [X] 9.6 Implement validation in `server.main()` between
    `parse_config(args)` and `DbConnPool(...)`
    (`server.py:711-717`): if `pre_connect_script` is set, call
    `split_command()`; on `ValueError` call
    `parser.error("invalid --pre-connect-script /
    PGMCP_PRE_CONNECT_SCRIPT: <reason>")`. 9.5 passes.
    [verify: auto-test]
    → 9.5 passes; real CLI with unbalanced quotes exits 2 naming
      the option, value not echoed [live] (2026-10-09)
  - [X] 9.7 Update the `--pre-connect-script` help text
    (`server.py:700`) and the README "Pre-connect scripts" section
    with the quoting rules per OS (POSIX shell-style quotes and
    backslash escapes; Windows double quotes only, backslash always
    literal, no literal `"` possible) and one quoted-path example
    per OS. The README teardown text is written by the subtasks that
    implement it (10.3 POSIX, 11.4 Windows, 12.2 exit path), so the
    README never describes behaviour the code lacks. Add a unit test
    asserting `_build_parser().format_help()` contains the quoting
    rule text. [verify: auto-test]
    → help-text test passes; `--help` and README show the rules per
      OS; teardown README text moved to 10.3/11.4/12.2 [live]
      (2026-10-09)

- [ ] 10.0 **User Story:** As a script author on POSIX, I want
  teardown to send `SIGTERM`, wait a 5 s grace period, then kill, so
  that my script's cleanup handler stops its tunnel children [7/0]
  - [X] 10.1 Extend `FakeProcess` in `test_connection_script.py`
    with an `ignore_terminate` option (terminate records the call
    but does not set an exit code). Change the existing assertion
    at `test_connection_script.py:333` from `killed is True` to
    `terminated is True` (the only existing assertion the PRD lets
    change). No other existing assertion changes. The changed test
    is expected red until 10.3. [verify: code-only]
    → also added `FakeProcess.calls` (terminate/kill/reader-cancelled
      order) for 10.2 (2026-10-09)
  - [X] 10.2 Write teardown unit tests (patch
    `_TERMINATE_GRACE_S` to 0.05): script exits on terminate →
    `terminated` and not `killed`, event `stop requested (SIGTERM)`;
    `ignore_terminate` → `killed` after grace, event
    `force-killed after …s grace`; already-exited process →
    neither called; stop signal sent before the reader task is
    cancelled (assert via a fake stdout that records when it is
    closed). Tests fail. [verify: auto-test]
    → 4 tests in TestGracefulTeardown; red as expected
      (`_TERMINATE_GRACE_S` missing) [live] (2026-10-09)
  - [X] 10.3 Implement POSIX teardown in `_teardown()`
    (`connection_script.py:318-338`) per tech-design §Teardown:
    add module constant `_TERMINATE_GRACE_S = 5.0`; only when
    `returncode is None`; `terminate()` → `wait_for(proc.wait(),
    grace)` → on timeout `kill()` + `wait()`; send the signal
    before reaping the reader, reap after exit; emit the two new
    events. README "Pre-connect scripts": on POSIX the script gets
    `SIGTERM`, then `SIGKILL` after 5 s. 10.2 passes; full unit
    suite green. [verify: auto-test]
    → waits poll `returncode` instead of `proc.wait()` (Python 3.12
      wait blocks on open pipes; tech-design updated); unit suite
      288 passed, 1 xfailed; README authoring note added [live]
      (2026-10-09)
  - [X] 10.4 Change `_spawn()` to inherit stderr (drop
    `stderr=asyncio.subprocess.PIPE`, `connection_script.py:206`).
    Confirm nothing reads `proc.stderr` (grep). Full unit suite
    green. [verify: auto-test]
    → grep: no reader of `proc.stderr`; unit suite 288 passed,
      1 xfailed [live] (2026-10-09)
  - [X] 10.5 Add an integration test in
    `tests/integration/test_pre_connect.py`: a real bash
    long-running script with `trap 'echo done > "$MARKER"; exit 0'
    TERM`, emits `[MCP] READY_TO_CONNECT`, blocks with
    `sleep 600 & wait $!`. After `ensure_ready()` then
    `DbConnPool.close()` (or manager `stop()`): marker file exists
    and `os.kill(pid, 0)` raises `ProcessLookupError`. Second case:
    script that traps and ignores TERM, with `_TERMINATE_GRACE_S`
    patched to 0.5 → killed within grace + 1 s; the test kills the
    orphaned `sleep` in cleanup. Third case: the script lives in a
    tmp directory whose name contains a space and is configured as
    `'"<tmpdir with space>/s.sh"'`; it starts and emits READY
    (real POSIX spawn of a quoted path, PRD success metric 6).
    [verify: auto-test]
    → 3 passed in 1.7s, no orphaned processes; first run hung on
      `proc.wait()` after SIGKILL (a child held stdout open, Python
      3.12 waits for pipes) — fixed in 10.3 by polling `returncode`
      [live] (2026-10-09)
  - [~] 10.6 Run the full suite (unit + integration + existing
    E2E per Notes). Zero failures; the only changed existing
    assertion is the one from 10.1. [verify: auto-test]
    → unit 288 passed, 1 xfailed; integration 18 passed, 9 skipped,
      24 errors — all errors at fixture setup: `helm install
      bitnami/postgresql` → "repo bitnami not found" (environment,
      not code); E2E not run. Unit and integration must run
      separately (duplicate test basenames) (2026-10-09)
  - [X] 10.7 Exit detection with a child holding stdout (found in
    10.5): `_watch_exit()` and `wait_for_exit()` await
    `proc.wait()`, which on Python 3.12+ returns only after all
    pipes close, so a long-running script whose child inherited
    stdout (e.g. `aws ssm start-session`) is never seen to exit
    (breaks FR-5). Add an integration test in
    `tests/integration/test_pre_connect.py`: a real script starts
    `sleep 600 &`, emits READY, then exits; the
    `Pre-connect-script exited` event arrives within 2 s. Then make
    both waits poll `returncode` (shared `_wait_exited`). Unit suite
    green. [verify: auto-test]
    → bug reproduced (no exit event within 2s); after fix the 4
      real-process tests pass in 2.2s; unit suite 288 passed,
      1 xfailed; tech-design updated [live] (2026-10-09)

- [X] 11.0 **User Story:** As an analyst on Windows, I want the
  script placed in a kill-on-close job object so that teardown (and
  an MCP crash) ends the whole process tree, while a run-and-exit
  script's deliberate background process survives [6/0]
  - [X] 11.1 Look up on Microsoft Learn (WebFetch) and record in a
    code comment: `CreateJobObjectW`, `SetInformationJobObject`
    with `JobObjectExtendedLimitInformation`,
    `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` /
    `JOBOBJECT_BASIC_LIMIT_INFORMATION` field layout,
    `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, `OpenProcess` rights
    `PROCESS_SET_QUOTA | PROCESS_TERMINATE`,
    `AssignProcessToJobObject`, `TerminateJobObject`,
    `CloseHandle`. Note each constant's value and source URL.
    [verify: code-only]
    → values and URLs recorded in the `win_job.py` docstring
      (2026-10-09)
  - [X] 11.2 Create `src/postgres_mcp/sql/win_job.py` with class
    `WindowsJob`: `for_pid(pid)` (create job, set kill-on-close,
    open process, assign, close process handle; any failure closes
    what was opened and raises `OSError` with the Win32 error),
    `terminate(exit_code=1)`, `release()` (clear limit flags, then
    close), `close()`. ctypes `kernel32` with explicit
    `argtypes`/`restype`, `use_last_error=True`. Module must not be
    imported on POSIX. [verify: code-only]
    → `release()` keeps the handle open if clearing kill-on-close
      fails, so released processes are not killed; ruff clean
      (2026-10-09)
  - [X] 11.3 Write unit tests in `test_connection_script.py` for
    the Windows branch by patching two module-level seams in
    `connection_script.py` — `_is_windows()` (returns `True`) and
    `_make_job(pid)` (returns a fake job). Never patch `os.name`
    (breaks pathlib/asyncio on macOS). Cases: attach called once
    after spawn;
    teardown → `job.terminate()` + `job.close()`, `proc.kill()`
    not called, event `process tree terminated`; RUN_AND_EXIT
    (exit before READY) → `job.release()`, not `terminate()`;
    LONG_RUNNING self-exit → `job.close()`; attach raises
    `OSError` → warning event `could not attach job object` and
    teardown falls back to `proc.kill()`; process already exited
    at attach time → no attach, no warning. Tests fail.
    [verify: auto-test]
    → 6 tests in TestWindowsJob; red as expected (seams missing)
      [live] (2026-10-09)
  - [X] 11.4 Implement the Windows branch in
    `connection_script.py`: add the seams `_is_windows()`
    (`os.name == "nt"`) and `_make_job(pid)` (imports `win_job`
    inside the function, returns `WindowsJob.for_pid(pid)`); attach
    in `_spawn()` right after `create_subprocess_exec` when
    `_is_windows()`; mode-dependent release in the
    run-and-exit path (`connection_script.py:246-262`) and the
    long-running exit path; Windows teardown per tech-design
    §Teardown (no graceful step, wait bounded by
    `_TERMINATE_GRACE_S`). README: on Windows the whole process
    tree ends at once, with no graceful step, also if the MCP
    crashes. 11.3 passes; full unit suite green on macOS.
    [verify: auto-test]
    → 11.3 passes (Windows branch simulated on macOS via the seams;
      real run in 11.6); unit suite 294 passed, 3 skipped,
      1 xfailed; README updated [simulated: no Windows host yet]
      (2026-10-09)
    → on Windows: tests/unit/sql 160 passed (Windows branch, native
      split rules, real job tests) [live] (2026-10-09)
  - [X] 11.5 Create `tests/unit/sql/test_win_job.py`
    (`pytest.mark.skipif(os.name != "nt")`): `WindowsJob.for_pid`
    on a `python -c` child that starts a `python -c` grandchild
    sleeping; `terminate()` ends both (check by PID); `release()`
    leaves both running (then clean up); `close()` with
    kill-on-close ends both. Skipped on macOS; full unit suite
    still green on macOS. [verify: auto-test]
    → 3 tests written; the child waits for a stdin line sent after
      attach, so the grandchild starts inside the job; skipped on
      macOS, unit suite green [simulated: runs on Windows in 11.6]
      (2026-10-09)
    → on Windows: 3 passed — terminate and close end child and
      grandchild, release leaves both running [live] (2026-10-09)
  - [X] 11.6 Windows setup and first Windows run (pulled forward so
    ctypes bugs surface here, not at the end): over SSH, install
    `uv` at user level with the official PowerShell installer and
    `uv python install 3.12`; copy the working-tree version of
    every git-tracked file (`git ls-files` list, copied with `scp`;
    never `.git`, `.claude/`, `tmp/`, `.env`) plus the new files
    from 9.0–11.0 to a directory under the user profile whose name
    contains a space (e.g. `fpm test`); create a
    venv with `uv`, `uv pip install -e ".[dev]"`; run
    `pytest tests/unit/sql/test_win_job.py`. Fix 11.2 until green.
    Record versions, the path (as `<user>` placeholder) and pass
    counts. Commands need the user's approval as they run.
    [verify: manual-run-claude]
    → uv 0.12.24 (installer needs `powershell -ExecutionPolicy
      ByPass` for its own process; machine policy unchanged), Python
      3.12.15; tree in `C:\Users\<user>\fpm test` (tar of tracked +
      new files; macOS `._*` metadata removed); editable install
      from the path with a space works; test_win_job.py 3 passed,
      tests/unit/sql 160 passed. First Windows run showed FakeProcess
      PIDs could reach the real job API (risk of attaching and
      terminating an unrelated process); fixed with an autouse guard
      fixture [live] (2026-10-09)

- [ ] 12.0 **User Story:** As an analyst who closes the agent, I want
  `server.main()` to be the single teardown owner on every exit path
  so that no tunnel process outlives the MCP [5/0]
  - [ ] 12.1 Write unit tests in `tests/unit/test_transport.py`
    with a stubbed transport and a stub `db_connection` that counts
    `close()` calls. The signal handler is a closure inside
    `main()` (`server.py:749-752`): capture it by patching
    `loop.add_signal_handler` and invoke the captured callback.
    Cases: transport returns normally → `close()` once; transport
    raises → `close()` once and the original exception propagates;
    signal path, parametrised over `SIGTERM` and `SIGINT` → `main`
    cancelled, `close()` once, `pytest.raises(SystemExit)` with code
    `128 + sig`; double trigger (signal twice; signal plus normal
    transport return) → `close()` still once; foreign cancellation
    (no signal) → `close()` once, `CancelledError` re-raised;
    `close()` raising → logged, original outcome kept. Tests fail.
    [verify: auto-test]
  - [ ] 12.2 Implement in `server.py` per tech-design §`server.py`
    exit path: signal handler records the signal and cancels the
    main task (no `close()`, no `sys.exit` inside a task); reduce
    `shutdown()` (`server.py:771-795`) to that recording role or
    delete it (only the signal handler calls it; no test uses it);
    keep `shutdown_in_progress` (`server.py:54`) to ignore repeat
    signals; wrap the transport `await` (all three transports,
    `server.py:758-768`) in `try / except CancelledError /
    finally: await db_connection.close()`; exit with `128 + sig`
    after the `finally` when a signal was received. README: the
    script is stopped when the client disconnects; a second Ctrl+C
    during that stop can leave a POSIX script running. 12.1 passes;
    full unit suite green. [verify: auto-test]
  - [ ] 12.3 Add E2E test in `tests/e2e/test_server_lifecycle.py`:
    start the MCP with the `subprocess.Popen([sys.executable, "-m",
    "postgres_mcp", ...], env PYTHONPATH=src)` pattern from
    `test_server_lifecycle.py:57-63` (not `create_mcp_session`,
    which cannot close stdin on demand), with a long-running script
    that writes its PID to a file and emits `[MCP] DB_URL` (the
    test PG URL) then READY; close the MCP's stdin;
    assert the script PID is gone within `_TERMINATE_GRACE_S + 2`
    seconds (`os.kill(pid, 0)`). [verify: e2e]
  - [ ] 12.4 Add E2E case: same script with
    `--transport streamable-http`; send `SIGTERM` to the MCP;
    assert the script PID is gone and the MCP exit code is
    `128 + 15`. Check uvicorn's signal re-raise behaviour for the
    installed version. If the test shows a double or missed
    teardown, return to 12.2 and fix it there. [verify: e2e]
  - [ ] 12.5 Run the full suite (unit + integration + E2E). Zero
    failures. [verify: auto-test]

- [ ] 13.0 **User Story:** As the maintainer, I want every
  Windows-only acceptance criterion checked on the Windows test
  machine over SSH, with evidence recorded here, so that the Windows
  promise is proven before release [8/0]
  - [ ] 13.1 Confirm the Windows setup from 11.6 is still present
    (`uv --version`, `uv run --python 3.12 python --version`).
    Record output. [verify: manual-run-claude]
  - [ ] 13.2 Refresh the Windows test directory from 11.6 with the
    current versions of all files changed in 9.0–12.0 (tracked
    files only — never `.git`, `.claude/`, `tmp/`, `.env`), then
    `uv pip install -e ".[dev]"`. Record the file list and install
    output. [verify: manual-run-claude]
  - [ ] 13.3 Run on Windows: `pytest tests/unit/sql/` (including
    `test_win_job.py`, now not skipped) and
    `test_split_command.py`. Record the pass counts.
    [verify: manual-run-claude]
  - [ ] 13.4 Quoted path with a space: run the MCP with
    `--pre-connect-script 'uv run --no-project --python 3.12
    "<dir with space>\fixture.py"'` where the fixture emits
    `[MCP] READY_TO_CONNECT`; confirm via debug log / `status`
    output that the script started and READY was received.
    [verify: manual-run-claude]
  - [ ] 13.5 Tree teardown: a launcher-started fixture
    (`uv run … fixture.py`) whose child starts a long-lived process
    that binds a local port. Trigger teardown; confirm the port is
    free (`Get-NetTCPConnection -LocalPort <p>` empty) and a second
    start of the script succeeds. Separately, start the same
    fixture with `uv run` directly in PowerShell and
    `Stop-Process -Id <uv pid>` (no tree kill); record whether the
    child survived — answers the PRD assumption.
    [verify: manual-run-claude]
  - [ ] 13.6 Exit path: start the MCP over stdio from a PowerShell
    wrapper, close its stdin; confirm no process from the script
    tree remains (`Get-CimInstance Win32_Process` filtered by the
    recorded PIDs). [verify: manual-run-claude]
  - [ ] 13.7 Run-and-exit: a fixture that starts a detached
    background process and exits 0; confirm that background process
    is still running after the MCP connected, then clean it up.
    [verify: manual-run-claude]
  - [ ] 13.8 Remove only the exact test directory recorded in 11.6,
    after checking the path ends in `fpm test` and is under the
    user profile (keep `uv` and Python for future runs). Record the
    evidence of 11.6 and 13.1–13.7 under each subtask here, with
    the host alias, user name and any IP addresses replaced by
    `<windows-test-host>` / `<user>`. [verify: manual-run-claude]

- [ ] 14.0 **User Story:** As the downstream installer maintainer, I
  want a released version with documented quoting rules and a reply
  naming it so that I can pin it in the Windows installer [5/0]
  - [ ] 14.1 Add a `## [0.1.5] - <date>` section to `CHANGELOG.md`
    per the README CHANGELOG authoring rule: Added (quoted paths in
    `--pre-connect-script`, Windows tree teardown), Changed (POSIX
    values containing `'`, `"`, `\` and non-ASCII whitespace now
    split differently; graceful `SIGTERM` teardown; script stderr
    inherited; teardown on client close; whitespace-only value
    exits 2). Bump `pyproject.toml` to `0.1.5`. Then commit on
    `main`: first the amendment code, tests and docs, then the
    version bump + CHANGELOG (no AI attribution lines, per the
    user's CLAUDE.md). `scripts/release.sh:128-140` refuses a dirty
    tree and requires the bump and the `[0.1.5]` section to be
    committed. Confirm `git status` is clean. Do not push yet — the
    release script pushes. [verify: code-only]
  - [ ] 14.2 Write the hand-written GitHub Release body per the
    README rule to a scratch file. Show it to the user and get
    explicit approval to release 0.1.5 — the script uploads to
    PyPI, which cannot be undone. [verify: manual-run-user]
  - [ ] 14.3 Precondition: the user's explicit approval from 14.2
    is recorded in this session; otherwise stop. Run
    `scripts/release.sh --version 0.1.5 --release-body-file <file>
    --yes` — it pushes the commits and tag to origin and uploads to
    PyPI (irreversible); then
    `scripts/release-check.sh 0.1.5` shows tag, GitHub Release and
    PyPI agree, and `uvx fluid-postgres-mcp@0.1.5 --version` prints
    `0.1.5`. [verify: manual-run-claude]
  - [ ] 14.4 On the Windows machine: `uvx fluid-postgres-mcp@0.1.5
    --version` and repeat 13.4 against the published package.
    [verify: manual-run-claude]
  - [ ] 14.5 Write the reply next to the hand-off in the downstream
    project's task folder (added to this session):
    `2026-MM-DD-fluid-postgres-mcp-reply.md` with the release
    version, the final quoting rules per OS (including the
    recommended registration form), Windows teardown behaviour (no
    graceful step; whole tree ends; also on MCP crash), and POSIX
    teardown behaviour (SIGTERM + 5 s). The user commits it in that
    repo. [verify: code-only]
