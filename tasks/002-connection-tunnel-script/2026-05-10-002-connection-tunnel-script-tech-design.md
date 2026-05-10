# 002-connection-tunnel-script: Long-Running Pre-Connect Script — Technical Design

**Status**: Draft
**PRD**: [2026-05-10-002-connection-tunnel-script-prd.md](./2026-05-10-002-connection-tunnel-script-prd.md)
**Created**: 2026-05-10

---

## Overview

Add a second mode to `--pre-connect-script`: **long-running mode**, in
which the script owns the tunnel for the lifetime of the MCP and
communicates with `DbConnPool` via a strict line-prefixed stdout
protocol. Mode is auto-detected from the script's behavior at launch.
Lifecycle and stdout parsing are extracted into a new
`ConnectionScriptManager` class composed by `DbConnPool`. The
existing run-and-exit codepath is preserved verbatim and exercised by
the existing test suite without modification.

## Current Architecture (RLM-verified)

Verified against code on 2026-05-10:

- `DbConnPool._run_pre_connect_hook()` spawns the script via
  `asyncio.create_subprocess_exec(*script.split(), ...)`, awaits
  `proc.communicate()` with `hook_timeout`, returns `bool`.
  Stdout/stderr are captured into bytes and dumped via `logger.debug`
  — verified via `src/postgres_mcp/sql/sql_driver.py:115-143`.
- `pool_connect()` calls `_run_pre_connect_hook()` once at line 170;
  on success creates the pool via `_create_pool()` (lines 145-157)
  which uses `psycopg_pool.AsyncConnectionPool` with `min_size=1`,
  `max_size=5`, `open=False` then `await pool.open()` and a sentinel
  `SELECT 1` — verified via `src/postgres_mcp/sql/sql_driver.py:145-188`.
- `_reconnect_loop()` runs an unbounded-or-bounded retry loop with
  exponential backoff (`min(initial_delay * 2 ** (attempt-1),
  max_delay)`), invoking `_run_pre_connect_hook()` at line 214 before
  each `_create_pool()` attempt — verified via
  `src/postgres_mcp/sql/sql_driver.py:190-228`.
- Reactive disconnect detection: `SqlDriver.execute_query()` catches
  exceptions; `_handle_pool_error()` checks
  `isinstance(e, (psycopg.OperationalError, OSError))` and calls
  `self.conn.mark_invalid(str(e))`; the next call to
  `ensure_connected()` triggers `_reconnect_loop()` — verified via
  `src/postgres_mcp/sql/sql_driver.py:287-331` (resolves the PRD's
  `[assumption, verify in tech-design]`).
- `ReconnectConfig.hook_timeout` default is **30.0** seconds, not 10
  as the PRD's "Current State" implied. Exposed as `--hook-timeout`
  and `PGMCP_HOOK_TIMEOUT` — verified via
  `src/postgres_mcp/config.py:15, 44`.
- `EventStore` exposes four categories: `ERROR`, `WARNING`, `EVENT`,
  `QUERY`. Three ring buffers are allocated (ERROR / WARNING / EVENT);
  `record(EventCategory.QUERY, ...)` is silently dropped — only
  `record_query(QueryRecord)` writes to the `_queries` deque —
  verified via `src/postgres_mcp/event_store.py:36-55`.
- `_emit()` invokes the user-supplied `on_event(msg: str)` callback;
  there is no built-in `EventCategory` selection at the
  `DbConnPool` layer — categorisation happens in whatever the
  callback does (`server.py` wires it to `event_store.record(EVENT,
  ...)` per the existing user story 7.3 design) — verified via
  `src/postgres_mcp/sql/sql_driver.py:111-113`.
- `obfuscate_password()` is the canonical password-redaction helper,
  used for URLs in URL form and connection strings in `key=value`
  form — verified via `src/postgres_mcp/sql/sql_driver.py:35-74`.

## Past Decisions (Claude-Mem)

- claude-mem #13055 (2026-05-09) — E2E SSM disruption suite was
  designed around run-and-exit scripts. The eight test scenarios
  (`TestSsmHappyPath`, `TestTunnelKill`, `TestConnectionKillViaSql`,
  `TestPgServiceStopStart`, `TestPgServiceRestart`) treat the script
  as a fast-exiting tunnel-opener. Long-running mode adds a parallel
  test class without modifying these.
- Task 001 user story 4.x — pre-connect-hook acceptance was
  exit-code-based; PATH lookup was tested explicitly. Long-running
  mode must continue to honour PATH lookup since the same `*.split()`
  argv parsing is reused.
- Task 001 user story 9.5 — integration test
  `tests/integration/test_pre_connect.py` writes a marker file from
  the script and verifies it post-connect. This test relies on
  run-and-exit and remains unchanged.

## Proposed Design

### Architecture

The change introduces one new component and modifies one existing
component:

- **New**: `ConnectionScriptManager` (in
  `src/postgres_mcp/sql/connection_script.py`). Owns: the script
  subprocess, the asyncio reader task, the protocol parser, the
  pending URL override, the `READY_TO_CONNECT` event signal, and the
  process-exit signal. Knows nothing about psycopg or pool state.
- **Modified**: `DbConnPool` composes `ConnectionScriptManager`. Its
  `pool_connect()` and `_reconnect_loop()` paths consult the manager
  instead of the inline `_run_pre_connect_hook()` (which is removed,
  with its run-and-exit semantics absorbed into the manager).

### Layering

| Layer | What lives here |
|---|---|
| `server.py` | Unchanged. Existing argparse for `--pre-connect-script` and `--hook-timeout` is reused. |
| `DbConnPool` | Owns connection-pool lifecycle. Delegates "is the tunnel up, what URL do I use" to `ConnectionScriptManager`. |
| `ConnectionScriptManager` | Owns script subprocess lifecycle, stdout parsing, mode detection, URL override, ready signal. |
| `EventStore` (unchanged) | Receives all script + connection events via the existing `on_event` callback wired in `server.py`. |

### Components

#### `ConnectionScriptManager` (new)

**Location**: `src/postgres_mcp/sql/connection_script.py`

**Public surface**:

```
class ScriptMode(str, Enum):
    NONE = "none"               # No script configured
    RUN_AND_EXIT = "run_and_exit"
    LONG_RUNNING = "long_running"

@dataclass
class ScriptOutcome:
    success: bool                          # may we attempt psycopg connect now?
    mode: ScriptMode
    db_url_override: Optional[str]         # last DB_URL line, if any
    error: Optional[str]                   # human-readable, password-obfuscated

class ConnectionScriptManager:
    def __init__(
        self,
        script: Optional[str],
        hook_timeout: float,
        on_event: Callable[[str], None],
    ): ...

    async def ensure_ready(self) -> ScriptOutcome:
        """
        Idempotent: returns once the script is ready for the MCP to
        attempt psycopg connect. In RUN_AND_EXIT mode, this means
        the script exited 0. In LONG_RUNNING mode, this means the
        most recent READY_TO_CONNECT line has been seen since the
        last call. Bounded by hook_timeout.
        """

    async def stop(self) -> None:
        """Best-effort terminate of any running script process."""

    @property
    def alive(self) -> bool:
        """True if a long-running script process is running."""

    async def wait_for_exit(self) -> int:
        """Await script process exit; returns exit code. Raises if no process."""
```

**Private state**:

- `_script: Optional[str]` — argv string from config
- `_hook_timeout: float`
- `_on_event: Callable[[str], None]`
- `_proc: Optional[asyncio.subprocess.Process]`
- `_reader_task: Optional[asyncio.Task]`
- `_mode: ScriptMode`
- `_ready_event: asyncio.Event` — set on each `READY_TO_CONNECT`,
  cleared by `ensure_ready()` after consumption
- `_db_url_override: Optional[str]` — last `DB_URL` payload received
- `_exit_event: asyncio.Event` — set when process exits

**Behavior**:

- **NONE**: `ensure_ready()` returns
  `ScriptOutcome(success=True, mode=NONE, db_url_override=None)`
  immediately. Backwards-compatible with current behavior when
  `pre_connect_script` is unset.
- **Mode detection on first `ensure_ready()` call**: Spawn the script
  via `asyncio.create_subprocess_exec(*self._script.split(), stdout=PIPE,
  stderr=PIPE)`. Spawn a background reader task. Wait for the first
  of: process exit, `READY_TO_CONNECT` line, `hook_timeout` expiry.
  - Exit before `READY_TO_CONNECT` → `_mode = RUN_AND_EXIT`. Return
    `success = (exit_code == 0)`. Tear down reader task. Process is
    gone.
  - `READY_TO_CONNECT` line before exit → `_mode = LONG_RUNNING`.
    Return `success=True`. Process and reader stay running.
  - `hook_timeout` expires before either → kill process, drain
    reader, set mode based on whether any protocol output was seen
    (`LONG_RUNNING` if so, conservative `RUN_AND_EXIT` if not),
    return `success=False, error="ready timeout"`.
- **Subsequent `ensure_ready()` calls**:
  - `RUN_AND_EXIT`: re-spawn script (process is gone), repeat the
    same exit-or-line-or-timeout race. Same mode is reused — no
    re-detection, even if the script's behavior changes.
  - `LONG_RUNNING` with `_proc` alive: clear `_ready_event` if it
    was set previously, then `await wait_for(ready_event,
    hook_timeout)`. If exit fires first → process died, set mode
    NONE/restart on next call, return `success=False`.
    `READY_TO_CONNECT` won → return `success=True`.
  - `LONG_RUNNING` with `_proc` dead: re-spawn script, run the same
    detection race as initial launch.
- **Reader task**: `async for line in self._proc.stdout` (psycopg
  unrelated; uses asyncio's line-buffered stream). For each line:
  - Strip trailing `\n`. UTF-8 decode with `errors="replace"`.
  - If line matches `^\[MCP\]\s+(\S+)(?:\s+(.*))?$` → dispatch to
    protocol handler (see §Data Models / Protocol).
  - Else → `logger.debug("[script:%s] %s", pid, obfuscate_password(line))`.
    Bounded by Python's logging which respects log levels and
    handlers; no in-memory accumulation.
- **Process exit detection**: A separate task, spawned alongside the
  reader, awaits `self._proc.wait()` and sets `_exit_event` plus
  emits a `script exited (code=N)` event. The reader task sees stream
  EOF and finishes naturally.

#### `DbConnPool` (modified)

**Changes**:

- `__init__` constructs a `ConnectionScriptManager` and stores it.
- `_run_pre_connect_hook()` is **deleted**. Its callers are updated:
  - `pool_connect()` (line 170): `outcome = await
    self._script_mgr.ensure_ready()`. If
    `outcome.db_url_override`, replace `self.connection_url` with
    it (but call `obfuscate_password()` before any logging). If
    `outcome.success` is False, transition to ERROR same as today.
  - `_reconnect_loop()` (line 214): same as above on every
    iteration. Backoff occurs *before* `ensure_ready()` — same
    ordering as today.
- New: a **proactive disconnect watcher**. After `pool_connect()`
  completes successfully and mode is `LONG_RUNNING`, spawn a watcher
  task that `await self._script_mgr._exit_event.wait()`. When fired:
  call `self.mark_invalid("pre-connect-script exited")`. This is what
  delivers the ≤1s detection from PRD NFR-1. The watcher is
  re-spawned after every successful reconnect.
- `close()`: calls `await self._script_mgr.stop()` so the script
  process is reaped on MCP shutdown.

#### `EventStore` (unchanged)

Per user's choice, all script lifecycle events flow through the
existing `_on_event` callback into `EventCategory.EVENT`. Crashes,
timeouts, and protocol errors emit a different *message* but the same
*category*. No schema change.

### Data Models

#### Protocol grammar

A "protocol line" is a line on the script's stdout matching the
extended-regex:

```
^\[MCP\]\s+(?P<keyword>[A-Z_]+)(?:\s+(?P<payload>.*))?\s*$
```

Two keywords are defined for v1:

| Keyword | Payload | Effect |
|---|---|---|
| `READY_TO_CONNECT` | none | Signals MCP may attempt psycopg connect now. Sets `_ready_event`. |
| `DB_URL` | URL string | Replaces `_db_url_override` for the next connect. URL is parsed by `urllib.parse.urlparse`; rejection (no scheme, no netloc) emits a warning event and the previous override is retained. |

Lines not matching the prefix are diagnostic output (debug log only,
password-obfuscated).

Unknown keywords matching the prefix are logged at WARNING level once
per keyword per session and otherwise ignored. This leaves room for
v2 extensions without breaking v1 scripts running against newer MCP
servers.

#### `ScriptOutcome`

Defined above in §Components. The single return type from
`ensure_ready()`. No partial states leak out of the manager.

#### Event messages emitted

All formatted with their dynamic parts password-obfuscated. Fixed
strings, suitable for grep / human review:

- `Pre-connect-script started (mode=run_and_exit, pid=N)`
- `Pre-connect-script started (mode=long_running, pid=N)`
- `Pre-connect-script DB_URL received (host=H, db=D)` — host and
  db are extracted from the parsed URL; password and full netloc
  are not in the message
- `Pre-connect-script DB_URL malformed: <reason>`
- `Pre-connect-script READY_TO_CONNECT received`
- `Pre-connect-script unknown keyword: KEYWORD (ignored)`
- `Pre-connect-script ready timeout after Ns`
- `Pre-connect-script exited (code=N)` — emitted on process exit in
  any mode
- `Pre-connect-script restart requested`

### API Design

No MCP tool surface changes. No CLI flag additions. No env var
additions. Exclusively internal API.

### Integration Points

- **psycopg-pool**: unchanged. The pool is created and torn down
  exactly as today (`AsyncConnectionPool(min_size=1, max_size=5)`).
- **`server.py` argparse / `parse_config`**: unchanged. Existing
  `--pre-connect-script` and `--hook-timeout` flow through to
  `ReconnectConfig` which is passed into `DbConnPool`, which
  forwards to `ConnectionScriptManager`.
- **`status` MCP tool**: unchanged. It queries `EventStore`, which
  receives all new lifecycle events via the existing `on_event`
  callback.
- **`obfuscate_password()`**: reused for every diagnostic log line
  and every event message that may carry URL fragments.

### Error Handling

Following the existing pattern in `DbConnPool`:

- All exceptions in the reader task are caught and logged. The
  reader never propagates exceptions back to `DbConnPool`. If the
  reader fails fatally (decoder bug, etc.), the watcher task sees
  process EOF and the manager treats it as script exit.
- All exceptions in the script process spawn (`FileNotFoundError`
  for missing executable, `PermissionError`) bubble up as
  `ScriptOutcome(success=False, error=str(e))`. Same path as today's
  `_run_pre_connect_hook` `except Exception` block.
- Malformed URLs in `DB_URL` lines do not abort the protocol;
  warning event, prior override retained.
- `hook_timeout` enforcement: every wait inside `ensure_ready()` is
  bounded by `asyncio.wait_for(..., timeout=hook_timeout)`. There is
  no path that blocks indefinitely (NFR-2).

### Testing Strategy

Per PRD NFR-5, this is the core component and demands very thorough
coverage. Tests are organised by layer:

#### Unit (`tests/unit/sql/test_connection_script.py`, new)

Mocked subprocess (`asyncio.create_subprocess_exec` patched, fed
canned stdout). One test per state-machine arc:

- mode detection: exit before READY → RUN_AND_EXIT
- mode detection: READY before exit → LONG_RUNNING
- mode detection: timeout before either → kill + failure
- run-and-exit success (exit 0)
- run-and-exit failure (exit 1)
- long-running: READY then later READY → both succeed
- long-running: READY then process exit → second `ensure_ready` re-spawns
- long-running: DB_URL then READY → outcome carries override
- long-running: DB_URL malformed → warning, no override
- long-running: unknown keyword → warning, ignored
- long-running: stdout flooded with non-protocol lines → no memory growth, no parse hits
- long-running: stdout EOF without exit → reader finishes, process treated as exiting
- protocol: prefix exactly `[MCP] ` (single space, bracket case-sensitive); leading whitespace before prefix → not protocol
- obfuscation: DB_URL with embedded password → password absent from any logged event message
- ensure_ready called concurrently → second call awaits first (or raises; tech-design choice deferred to implementation, with a test asserting the chosen behaviour)

#### Unit (`tests/unit/sql/test_pre_connect_hook.py`, modify)

Existing tests must continue to pass unmodified — they exercise the
RUN_AND_EXIT path now living in `ConnectionScriptManager`.
`DbConnPool` no longer has `_run_pre_connect_hook`; tests calling
that method directly (if any) must be moved to the new file or
re-routed.

#### Integration (`tests/integration/test_pre_connect.py`, modify)

Existing run-and-exit marker-file test continues unchanged.
**New**: long-running counterpart — script that opens a TCP listener
on a free port, emits `[MCP] DB_URL postgresql://...` and `[MCP]
READY_TO_CONNECT`, stays alive. MCP connects. Script is killed; MCP
detects exit within 1s (assert via EventStore timestamp delta), then
re-spawns and reconnects.

#### E2E (`tests/e2e/test_long_running_script.py`, new)

- Mirror of `tests/e2e/test_mcp_smoke.py` but with a long-running
  bash script (no SSM) that opens a TCP socat to local PG and emits
  `[MCP] READY_TO_CONNECT`. Verifies the protocol handshake against a
  real MCP subprocess.
- New SSM scenario in `tests/e2e/test_ssm_disruption.py`: a
  long-running variant of the SSM tunnel script (alongside, not
  replacing, `create_tunnel_script`). Tests:
  - happy path with `[MCP] DB_URL` emitted at runtime (replacing a
    deliberately-wrong placeholder URL passed via `--database-url`)
  - external tunnel kill → script detects (its `wait` on the
    backgrounded SSM session returns) → script exits → MCP detects
    in <1s → script restarts → reconnect
  - "credential rotation": script restart emits a different
    `DB_URL` (simulated by re-fetching the SSM Parameter Store
    value) → next reconnect uses new URL

#### Backwards-compatibility regression

- All 22 E2E tests in `tests/e2e/test_ssm_disruption.py` and
  `tests/e2e/test_*.py` pass unmodified.
- All 24 integration tests pass unmodified.
- All ~150 unit tests pass unmodified.

### Verification Approach

| Requirement | Method | Scope | Expected Evidence |
|---|---|---|---|
| FR-1: long-running mode auto-detection | `auto-test` | unit | `test_connection_script.py::test_mode_detection_*` (3 tests) pass |
| FR-2: line-prefixed protocol | `auto-test` | unit | grammar tests (~6 tests) pass |
| FR-3: URL override | `auto-test` | unit + integration | unit tests + integration test asserting `_create_pool` called with overridden URL |
| FR-4: connect-on-READY trigger | `auto-test` | unit | unit test asserting no `_create_pool` before READY in long-running mode |
| FR-5: instant disconnect on script exit | `auto-test` | integration + e2e | timestamp delta < 1s between script exit and `mark_invalid` event in EventStore |
| FR-6: hook_timeout on every READY wait | `auto-test` | unit | timeout tests (initial + post-disconnect) |
| FR-7: backwards compat | `auto-test` | full suite | existing 22 E2E + 24 integration + 150 unit tests pass without code changes |
| FR-8: full EventStore coverage | `auto-test` | unit | introspection test asserting every state transition emits exactly one event |
| NFR-1: detection latency ≤1s | `auto-test` | integration + e2e | assertion in tests as above |
| NFR-2: no silent hangs | `code-only` | review | static check that every `await` on the script's stdout/event is wrapped in `asyncio.wait_for` or paired with the exit watcher |
| NFR-3: bounded stdout memory | `auto-test` | unit | flood-stdout-with-1MB-of-non-protocol-output test, assert manager memory delta < threshold |
| NFR-4: credential safety | `auto-test` | unit | event-content tests assert no password substring in any emitted event message after a `DB_URL` line carrying a password |
| NFR-5: thorough coverage | `code-only` | review | this entire test plan applied |

## Trade-offs

### Considered Approaches

**Option A — Add a new flag `--connection-script` for long-running mode**

- Pros: explicit; mode is a config decision, not a behavior decision.
- Cons: doubles the API surface; user has to know which flag to use;
  forces a migration for any future user who wants to switch their
  script style.
- Rejected: PRD pinned auto-detection as the design.

**Option B — Auto-detect from script behavior (Recommended, chosen)**

- Pros: single flag, no config flag-day; existing scripts unchanged;
  script author decides the mode by what their script *does*.
- Cons: detection rule has edge cases (script that prints
  `[MCP] READY_TO_CONNECT` then exits immediately is a weird hybrid;
  resolved by treating "exited before we returned from
  `ensure_ready`" as RUN_AND_EXIT regardless of what was printed).
- Why recommended: matches user's stated direction, minimises CLI
  bloat, makes the migration story trivial (no migration).

**Option C — Embed long-running protocol parsing inside `DbConnPool`**

- Pros: fewer files; no new class; everything connection-related in
  one place.
- Cons: `DbConnPool` becomes a god-class mixing pool concerns with
  subprocess-management concerns. Untestable in isolation.
- Rejected: user's choice was explicit — extract a manager class.

## Implementation Constraints

### From Existing Architecture (RLM)

- `DbConnPool._run_pre_connect_hook` and `_reconnect_loop` are
  awaited from production code paths and mocked in tests. Removing
  the helper is fine; renaming or refactoring its public callers
  would cascade through tests. Keep `pool_connect()`,
  `_reconnect_loop()`, `mark_invalid()`, `ensure_connected()`,
  `close()` signatures unchanged.
- `on_event` callback is the only path from `DbConnPool` to
  `EventStore`. The new `ConnectionScriptManager` accepts the same
  callable type and calls it directly, so server-side wiring is one
  callback for both. No new constructor parameters in `server.py`.
- The script command-string is split via `shlex`-equivalent
  `script.split()` today (whitespace-only). For consistency, the
  manager preserves this — even though `shlex.split()` would be
  safer for paths with spaces. Out of scope for this change.

### From Past Experience (Claude-Mem)

- Task 001 #12977 (2026-05-09) — IAM split for SSM SendCommand. The
  long-running E2E variant must not require new IAM grants beyond
  what the existing 11.x suite already needs.
- Task 001 #12931 (2026-05-09) — reconnect attempts were limited in
  bad-connection E2E tests to avoid flaky long retries. The
  long-running mode tests will set
  `--reconnect-max-attempts` low (3-5) for the same reason.

## Files to Create / Modify

### Create

- `src/postgres_mcp/sql/connection_script.py` — `ConnectionScriptManager`,
  `ScriptMode`, `ScriptOutcome`, the line-protocol regex.
- `tests/unit/sql/test_connection_script.py` — full unit suite.
- `tests/e2e/test_long_running_script.py` — local long-running script
  E2E (no SSM).

### Modify

- `src/postgres_mcp/sql/sql_driver.py` — delete
  `_run_pre_connect_hook`; instantiate `ConnectionScriptManager` in
  `DbConnPool.__init__`; reroute `pool_connect` and `_reconnect_loop`;
  add the proactive-exit watcher; update `close()`.
- `tests/integration/test_pre_connect.py` — add long-running
  counterpart test class.
- `tests/e2e/test_ssm_disruption.py` — add long-running SSM scenarios
  (new `TestLongRunningSsm*` classes; existing classes unchanged).
- `tests/e2e/ssm_fixtures.py` — add a sibling
  `create_long_running_tunnel_script()` helper alongside
  `create_tunnel_script()`. Existing helper unchanged.

### Not Modified

- `src/postgres_mcp/server.py` — no argparse, no `parse_config`,
  no tool-registration changes.
- `src/postgres_mcp/config.py` — `ReconnectConfig` schema unchanged.
- `src/postgres_mcp/event_store.py` — no new categories, no new fields.
- All existing unit tests for `_run_pre_connect_hook` (the test file
  remains; its tests now exercise the same behaviors via the new
  manager — see Testing Strategy).

## Dependencies

### External

None. `asyncio.subprocess`, `asyncio.Event`, and standard library
regex are sufficient. No new pip dependency.

### Internal

- `postgres_mcp.config.ReconnectConfig` — read for `pre_connect_script`
  and `hook_timeout`.
- `postgres_mcp.sql.sql_driver.obfuscate_password` — relocate to a
  module-level helper accessible from `connection_script.py`. Either
  move it to a new `postgres_mcp.sql.utils` module, or import from
  `sql_driver` (creates a circular-import risk; the move is safer).
  Decision: move to `postgres_mcp.sql.utils`, re-export from
  `sql_driver` for backwards compatibility.

## Security Considerations

- **Password leak in logs**: every line written to the debug logger
  must pass through `obfuscate_password()`. Test asserts this for
  the scripted case where a script prints its own raw URL.
- **Password leak in events**: `DB_URL`-related events must emit the
  parsed host/db/user but never the password. Tested in NFR-4.
- **Subprocess injection**: `pre_connect_script` is split on
  whitespace; a malicious config value could inject arguments. This
  matches today's behavior; no new attack surface. Documented in
  README, not enforced in code.
- **Process leak on MCP crash**: `close()` reaps the script; an
  abnormal MCP exit (signal kill -9) leaves the script orphaned. The
  script is responsible for handling EOF on its stdout (which it will
  see when MCP's pipe end closes). This is the script author's
  problem, documented in README.

## Performance Considerations

- Reader task does line-buffered reads from the subprocess; cost is
  negligible compared to query I/O.
- `[MCP]` regex is compiled once per manager instance.
- `_db_url_override` and `_ready_event` are O(1) state; no growth
  with session length.
- Memory bound: NFR-3 verified by test. The reader does not buffer
  full output; each line is immediately classified and either
  discarded (debug log handler decides) or consumed.

## Rollback Plan

The change is fully internal (no DB schema, no protocol with the
client, no config breaking change). To roll back:

1. Revert the commit that adds `connection_script.py` and modifies
   `sql_driver.py`.
2. Existing run-and-exit users see no difference — `_run_pre_connect_hook`
   is restored.
3. Long-running scripts written against this feature stop working;
   their `[MCP]` output becomes uninterpreted noise on stdout, and
   without a process exit the MCP times out per pre-existing
   `hook_timeout`.

## References

### Code (RLM)

- `src/postgres_mcp/sql/sql_driver.py:115-228` — current
  `_run_pre_connect_hook`, `pool_connect`, `_reconnect_loop`.
- `src/postgres_mcp/sql/sql_driver.py:287-331` — reactive disconnect
  detection chain.
- `src/postgres_mcp/sql/sql_driver.py:35-74` —
  `obfuscate_password()`.
- `src/postgres_mcp/event_store.py:36-62` — `EventStore` API.
- `src/postgres_mcp/config.py:9-15` — `ReconnectConfig`.
- `tests/e2e/ssm_fixtures.py:222-259` — `create_tunnel_script`
  template for the long-running sibling helper.

### History (Claude-Mem)

- claude-mem #13055 — E2E SSM disruption suite structure.
- claude-mem #13072 — MCP server direct integration pattern.
- claude-mem #12931 — reconnect-attempt limit in bad-connection
  E2E tests; will be applied to the long-running E2E tests too.

---

**Next Steps**:

1. Review and approve design.
2. Run `/dev:tasks` for TDD-style task breakdown — expected user
   stories: (1) extract & test `ConnectionScriptManager`,
   (2) integrate into `DbConnPool` with proactive watcher,
   (3) integration test parity, (4) long-running E2E (local),
   (5) long-running E2E (SSM), (6) regression suite green.
