# 002-connection-tunnel-script: Long-Running Pre-Connect Script — Technical Design

**Status**: Draft
**PRD**: [2026-05-10-002-connection-tunnel-script-prd.md](./2026-05-10-002-connection-tunnel-script-prd.md)
**Created**: 2026-05-10
**Amended**: 2026-10-08 — Windows and paths with spaces (PRD user
stories 7–10, FR-9–FR-12, NFR-6)

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

The 2026-10-08 amendment changes four things around that core:

- the command string is split by a quote-aware `split_command()`
  with one rule set for POSIX and one for Windows (FR-9);
- `_teardown()` becomes terminate → 5 s grace → kill on POSIX (FR-10);
- on Windows the script is placed in a job object with
  kill-on-close, and teardown terminates the job (FR-11);
- `server.main()` runs pool/script teardown exactly once in a
  `finally` around the transport, so it also runs when the client
  closes stdio and on Windows (FR-12).

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

Verified against code on 2026-10-08 (inputs to the amendment):

- `ConnectionScriptManager._spawn()` passes `*self._script.split()`
  to `asyncio.create_subprocess_exec` — verified via
  `src/postgres_mcp/sql/connection_script.py:198-210`. Spawn errors
  (`FileNotFoundError`, `PermissionError`, `OSError`) become
  `_SpawnError` (line 208-210, class at 395).
- `_teardown()` is the single kill point: drains the exit emitter,
  reaps reader/watcher tasks, then `proc.kill()` + `proc.wait()` if
  `returncode is None`; no-op when `_proc` is `None` — verified via
  `connection_script.py:318-338`. Callers: ready timeout (line 238),
  `stop()` (line 110-111).
- `DbConnPool.close()` cancels the exit watcher, awaits
  `_script_mgr.stop()`, then closes the pool; a second call is a
  no-op — verified via `src/postgres_mcp/sql/sql_driver.py:249-262`.
- The only shutdown route is `shutdown()` → `db_connection.close()`,
  wired via `loop.add_signal_handler` for `SIGTERM`/`SIGINT`. On
  Windows that raises `NotImplementedError`, which is caught and
  logged. The transport `await` (`run_stdio_async` etc.) has no
  `finally` — verified via `src/postgres_mcp/server.py:747-768,
  771-795`. Resolves PRD current-state bullet on the exit path.
- The option is `ReconnectConfig.pre_connect_script: Optional[str]`,
  read from `--pre-connect-script` or `PGMCP_PRE_CONNECT_SCRIPT`
  (empty string → `None`) — verified via
  `src/postgres_mcp/config.py:14, 43` and `server.py:700`.
- Python documents: `Process.terminate()` sends `SIGTERM` on POSIX
  and calls `TerminateProcess()` on Windows; `kill()` sends
  `SIGKILL` on POSIX and is an alias for `terminate()` on Windows;
  `create_subprocess_exec(**kwds)` forwards extra keywords to
  `Popen` — verified via Context7, Python 3.10
  `asyncio-subprocess.html` / `subprocess.html`, 2026-10-08.
- `tests/unit/sql/test_connection_script.py:99-102`:
  `FakeProcess.terminate()` sets the exit code immediately;
  `:320-333` asserts `killed is True` after a ready timeout — this
  assertion changes under FR-10 (allowed by the PRD constraint).
- PRD assumption "launcher children survive `TerminateProcess`" was
  **not resolved**: the Windows test machine has no `uv` and no
  Python (2026-10-08, `uv --version` / `python --version` over SSH:
  "not recognized" / "Python was not found"). The design does not
  depend on the answer — it kills the job in every case — so the
  check moves to the Windows verification in the task list.

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
| `server.py` | Existing argparse for `--pre-connect-script` and `--hook-timeout` is reused. Amendment: validates the command with `split_command()` at startup; runs teardown once in a `finally` around the transport. |
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

#### `split_command()` (new, amendment — FR-9)

**Location**: `src/postgres_mcp/sql/connection_script.py`, module
level.

```
def split_command(command: str, *, windows: Optional[bool] = None) -> list[str]:
    """Split a --pre-connect-script value into argv.
    windows=None → use os.name == "nt". Raises ValueError on
    unbalanced quotes or an empty result; the message never contains
    the command value."""
```

- **POSIX rules**: `shlex.split(command)` (POSIX mode, comments
  off — the `shlex.split` default). `ValueError` from shlex
  ("No closing quotation") is re-raised with a fixed message.
- **Windows rules** (pure Python, ~20 lines, testable on any OS):
  - space and tab outside double quotes separate arguments (the
    same separator set as the Windows C runtime);
  - `"` toggles quoting and is not copied into the argument;
  - an argument exists once any character *or* a quote was seen, so
    `""` yields one empty argument;
  - backslash is always a literal character (`"C:\dir\"` →
    `C:\dir\`); `'` is a literal character;
  - unbalanced `"` → `ValueError`.
  A literal `"` inside an argument cannot be expressed; Windows paths
  cannot contain `"`, so nothing a user needs is lost.
- **Round trip on Windows**: the argv list goes to `Popen`, which
  rebuilds the command line with `subprocess.list2cmdline`; that
  quotes arguments containing spaces and escapes backslashes before
  quotes, so the child sees the intended argv.
- **Compatibility**: a value with none of `'`, `"`, `\` whose
  separators are ASCII space/tab (POSIX also CR/LF) gives the same
  list as `str.split()` under both rule sets. On Windows, unquoted
  values with backslashes also match. Other whitespace (`\x0b`,
  `\x0c`, `\xa0`, …) is no longer a separator — `str.split()` split
  on it, `shlex` does not (checked 2026-10-09:
  `shlex.split('a\x0cb') == ['a\x0cb']`). Noted in the CHANGELOG
  together with the quote/backslash change.

#### Teardown (modified `_teardown`, amendment — FR-10, FR-11)

Applies only when `_proc is not None and _proc.returncode is None`;
run-and-exit processes that already exited are untouched.

- **Order**: the stop signal is sent *before* the reader task is
  reaped, so the script's stdout keeps draining while its `SIGTERM`
  handler runs; the reader is reaped after the process exits.
- **Waiting for exit**: teardown polls `proc.returncode` (every 50 ms,
  bounded by `_TERMINATE_GRACE_S`) instead of awaiting `proc.wait()`.
  Since Python 3.12 a `wait()` that started before the exit is woken
  only after all pipes close (CPython `asyncio/base_subprocess.py`
  `_call_connection_lost`), which never happens while a child of the
  script holds its stdout — found in 10.5, where the wait hung forever
  after `SIGKILL`. The wait after `kill()` is bounded too; if the
  process is still there, an event names its pid. The same polling
  (`_wait_exited`, no time limit) backs the exit watcher
  `_watch_exit()` and `wait_for_exit()`; with `proc.wait()` a
  long-running script whose child inherited stdout was never seen to
  exit, which broke FR-5 on Python 3.12+ (10.7).
- **POSIX**: emit `stop requested`; `proc.terminate()` (SIGTERM);
  wait for exit for up to `_TERMINATE_GRACE_S` with
  `_TERMINATE_GRACE_S = 5.0` (module constant, patched in tests);
  on timeout emit `force-killed after 5s`, `proc.kill()`,
  `await proc.wait()`.
- **Windows**: if a job is attached, `job.terminate()`
  (`TerminateJobObject`) ends the whole tree at once; emit
  `process tree terminated`. If no job is attached (attach failed),
  `proc.kill()` as today. Then `await proc.wait()` bounded by
  `_TERMINATE_GRACE_S`. No graceful step on Windows (PRD story 9).
- **Job lifetime depends on mode** (closing a kill-on-close job ends
  every process still in it):
  - **RUN_AND_EXIT** (script exited before READY): call
    `job.release()` — clears the kill-on-close limit, then closes
    the handle — so background processes the script left running on
    purpose (e.g. a detached tunnel) survive, exactly as on POSIX.
  - **LONG_RUNNING** script exited on its own: `job.close()` with
    kill-on-close still set, which ends orphaned descendants (a
    leftover tunnel would otherwise hold the port the restarted
    script needs).
  - **Teardown**: `job.terminate()` then `job.close()`.
  Every path closes the handle, so handles do not leak across
  restarts.
- **stderr**: the script's stderr is inherited by the MCP process
  instead of `PIPE`. Today `stderr=PIPE` is never read
  (`connection_script.py:206`), so a script writing more than one
  pipe buffer to stderr blocks — which now also matters during the
  grace window. Inherited stderr lands in the agent's MCP log, where
  the script's diagnostics are useful.

#### `WindowsJob` (new, amendment — FR-11)

**Location**: `src/postgres_mcp/sql/win_job.py` (imported only when
`os.name == "nt"`).

```
class WindowsJob:
    @classmethod
    def for_pid(cls, pid: int) -> "WindowsJob": ...  # raises OSError
    def terminate(self, exit_code: int = 1) -> None: ...  # TerminateJobObject
    def release(self) -> None: ...  # clear limit flags, then close
    def close(self) -> None: ...    # CloseHandle (kill-on-close applies)
```

Win32 constants and the `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`
layout are confirmed against Microsoft Learn during implementation
and pinned by `test_win_job.py` on Windows; they are not restated
here.

- `for_pid`: `CreateJobObjectW(NULL, NULL)`;
  `SetInformationJobObject(JobObjectExtendedLimitInformation,
  LimitFlags=JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)`;
  `OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, pid)`;
  `AssignProcessToJobObject`; close the process handle. Any failing
  call → close what was opened, raise `OSError` with the Win32 error.
- Uses `ctypes.windll.kernel32` with explicit `argtypes`/`restype`
  and `use_last_error=True`; no third-party dependency.
- Kill-on-close means that if the MCP process itself is
  force-killed or crashes, Windows closes the job handle and ends
  the script tree — covering the case FR-12's `finally` cannot.
- Nested jobs are supported from Windows 8 / Server 2012 ("A process
  can be associated with more than one job starting in Windows 8" —
  Microsoft Learn, `AssignProcessToJobObject`), so attaching works
  even when the MCP itself runs inside the agent's job. Assignment
  still fails if an enclosing job has UI limits or is terminating;
  that is handled by the failure path below.
- **PID reuse**: not a risk. `Popen` keeps an open handle to the
  process, and Windows does not reuse a PID while any handle to the
  process is open.
- **Race**: children the script starts between `CreateProcess` and
  `AssignProcessToJobObject` are not in the job. `CreateProcess`
  runs inside `_WindowsSubprocessTransport(...)`, after which the
  loop awaits pipe setup before `create_subprocess_exec` returns —
  verified via CPython `asyncio/windows_events.py:385-402` (3.9).
  The window therefore spans a few event-loop iterations, not a
  fixed sub-millisecond interval; under load it is longer. A
  launcher (`uv run …`) must resolve and start Python before its
  child exists, which normally takes far longer. Accepted; the
  Windows port-free check is the safety net. `CREATE_SUSPENDED`
  would close the window but `Popen` does not expose the thread
  handle needed to resume, so it is rejected.
- Called from `_spawn()` right after `create_subprocess_exec`, on
  Windows only. If the script has already exited
  (`proc.returncode is not None`), attaching is skipped silently.
  `OSError` → warning event `could not attach job object (…) —
  child processes may survive teardown (enclosing job restrictions
  or access denied)`, script keeps running, teardown falls back to
  `proc.kill()`.

#### Spawning on Windows: `ThreadedProcess` (new, amendment — FR-9, FR-11)

**Problem (found 2026-10-09 during 12.x prep):** `postgres_mcp.main()`
sets `WindowsSelectorEventLoopPolicy` on Windows, because psycopg's
async mode needs a selector loop there (`__init__.py:10-14`). On
Windows the selector loop cannot run subprocesses: "On Windows,
ProactorEventLoop supports subprocesses, while SelectorEventLoop does
not" (Python docs, asyncio platform support).
`create_subprocess_exec` raises `NotImplementedError` — reproduced on
the Windows test machine. So the pre-connect script never started on
Windows, in any released version.

**Design (user decision 2026-10-09, option A):** on Windows the
manager starts the script with `subprocess.Popen` wrapped in
`ThreadedProcess` (`src/postgres_mcp/sql/threaded_process.py`), which
offers the subset of `asyncio.subprocess.Process` the manager uses:

```
class ThreadedProcess:
    @classmethod
    def start(cls, argv: list[str]) -> ThreadedProcess  # Popen, stdout=PIPE, stdin=DEVNULL
    pid: int
    returncode: Optional[int]     # property, calls Popen.poll()
    stdout                        # async iterator of bytes lines
    def terminate(self) -> None
    def kill(self) -> None
```

- A daemon thread reads `stdout` line by line and hands each line to
  the event loop with `loop.call_soon_threadsafe` into an
  `asyncio.Queue`; EOF puts a sentinel. The manager's reader loop is
  unchanged (`async for raw in proc.stdout`).
- Exit detection already polls `returncode` (`_wait_exited`), so it
  works with `Popen` unchanged.
- `stdin=DEVNULL`: on the stdio transport the MCP's own stdin carries
  the protocol; the script must not be able to read it.
- Selection: `_is_windows()` → `ThreadedProcess.start(argv)`,
  otherwise `asyncio.create_subprocess_exec` as before. POSIX is
  unchanged.
- Side effect: `Popen` returns straight after `CreateProcess`, with no
  event-loop iterations before the job is attached, so the attach
  race (§`WindowsJob`) shrinks to the time between two Python
  statements.
- Rejected: a second Proactor loop in a thread (more moving parts,
  two loops to keep in step); Proactor for the whole MCP (psycopg's
  async mode does not support it on Windows).

#### `server.py` exit path (modified, amendment — FR-9, FR-12)

- **Startup validation**: in `main()`, between `parse_config(args)`
  and the `DbConnPool(...)` construction (`server.py:711-717`), if
  `pre_connect_script` is set, call `split_command()`. On
  `ValueError`: `parser.error("invalid --pre-connect-script /
  PGMCP_PRE_CONNECT_SCRIPT: <reason>")` — argparse prints usage and
  exits with status 2 (its usage-error convention); the value is not
  echoed. Validating the resolved config (not an argparse `type=`)
  covers the env-var form. Behaviour change: a whitespace-only value
  now exits 2 at startup instead of failing at spawn. `_spawn()`
  still calls `split_command()` and maps `ValueError` to
  `_SpawnError`, as a second line of defence for code paths that
  bypass `main()`.
- **Single teardown owner**: `main()` is the only place that tears
  down. The signal handler no longer calls `close()`; it records the
  signal and cancels the main task. The transport `await` (all three
  transports) is wrapped:
  ```
  try:
      await <transport>
  except asyncio.CancelledError:
      if received_signal is None: raise   # foreign cancellation
  finally:
      await db_connection.close()   # idempotent; second call is a no-op
  exit with 128 + signal if a signal was received
  ```
  `shutdown_in_progress` and the `128 + sig` exit code are kept;
  `sys.exit` no longer runs inside a task; `shutdown()` is removed.
  Signal handlers are installed **before** the initial
  `pool_connect()`, and that connect sits inside the same `try`: the
  pre-connect script starts there, and the first connect to an
  unreachable database can take the pool's full timeout (found in
  12.3: a `SIGTERM` in that window used to kill the MCP with no
  teardown). A signal that arrives after the transport returned only
  sets the exit code; it does not cancel `main` again, so it cannot
  interrupt `close()`.
- **Failed connect closes its pool** (12.6): `_create_pool()` closes
  the psycopg pool (`close(timeout=1.0)`) when `open()` or the first
  query fails or is cancelled. Before, the pool's workers kept
  reconnecting after every failed attempt and kept the process alive
  after shutdown. The `finally` covers stdio
  EOF, transport errors, signals, `KeyboardInterrupt` (cancellation
  of `main` by `asyncio.run`), and Windows, where no signal handler
  exists.
- **Second cancellation during teardown** (e.g. a second Ctrl+C):
  teardown is interrupted. On Windows the job's kill-on-close still
  ends the script tree when the MCP exits; on POSIX the script may be
  left running. Accepted and documented; teardown is bounded by the
  5 s grace in the normal case.
- **HTTP transports**: uvicorn installs its own `SIGINT`/`SIGTERM`
  handlers while serving, shuts down gracefully, returns from
  `serve()`, and may re-raise the captured signal afterwards. The
  `finally` runs teardown when `serve()` returns; a re-raised signal
  reaching our handler then cancels a `main` that is already
  finishing, and `close()` is a no-op the second time. The exact
  uvicorn re-raise behaviour for the pinned version is checked in
  implementation and pinned by an e2e test (below).

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
- `Pre-connect-script stop requested (SIGTERM)` — POSIX teardown start
- `Pre-connect-script force-killed after Ns grace` — POSIX, grace expired
- `Pre-connect-script process tree terminated` — Windows, job terminated
- `Pre-connect-script could not attach job object (<win32 error>) — child processes may survive teardown`

### API Design

No MCP tool surface changes. No CLI flag additions. No env var
additions. Exclusively internal API.

Amendment: the *meaning* of the existing `--pre-connect-script` /
`PGMCP_PRE_CONNECT_SCRIPT` string changes — it is now split with
quote rules (FR-9). New exit status 2 for an unbalanced-quote value.
Both are documented in README, `--help`, and CHANGELOG.

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
- Teardown wait (amendment) is bounded by `_TERMINATE_GRACE_S` plus
  the final `kill()`/job terminate; a ready timeout therefore ends at
  most `hook_timeout + _TERMINATE_GRACE_S` + kill time after the wait
  began.
- Job-object failures never stop the script from running; they
  degrade to `proc.kill()` with a warning event.
- The `finally` in `main()` swallows and logs exceptions from
  `close()` (as `shutdown()` does today) so it never masks the
  original transport exception.

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

#### Amendment tests (2026-10-08)

- **Unit, `split_command`** (new `tests/unit/sql/test_split_command.py`):
  table-driven, `windows=False` and `windows=True` explicitly so both
  rule sets run on macOS/Linux: compatibility rows (values with no
  `'`, `"`, `\` equal `str.split()`), POSIX quoted path with space,
  Windows quoted path with space, Windows unquoted backslashes,
  `""` → empty argument, `"C:\dir\"` → `C:\dir\`, unbalanced quotes
  → `ValueError` whose message does not contain the value, empty /
  whitespace-only → `ValueError`, `\x0c` / `\xa0` are not separators
  (pins the documented change).
- **Unit, teardown** (`test_connection_script.py`): `FakeProcess`
  gains an `ignore_terminate` option. Cases: exits on terminate →
  `terminated` and not `killed`; ignores terminate → `killed` after
  patched grace; already exited → neither called; Windows branch
  with a fake job → `job.terminate()` called, `proc.kill()` not;
  attach failure → warning event and `proc.kill()` fallback;
  RUN_AND_EXIT with a fake job → `job.release()` called, not
  `terminate()`/`close()`; LONG_RUNNING self-exit → `job.close()`;
  script already exited at attach time → no attach, no warning;
  stop signal sent before the reader is reaped. The existing timeout
  test's `killed is True` becomes `terminated is True`.
- **Unit, exit path** (`tests/unit/test_transport.py`): transport
  returns normally → `close()` awaited once; transport raises →
  `close()` awaited once and the original exception propagates;
  signal → main cancelled, `close()` awaited once, exit code
  `128 + sig`; foreign cancellation → re-raised after `close()`.
- **Unit, startup validation** (`tests/unit/test_config.py` or
  `test_transport.py`, whichever already drives `main()`): flag and
  env-var forms with unbalanced quotes → exit 2, stderr names the
  option, not the value.
- **Integration, POSIX graceful stop** (`test_pre_connect.py`): a
  real long-running script whose `SIGTERM` trap writes a marker file;
  the script blocks with `sleep N & wait $!` (a foreground `sleep`
  would delay the trap until it ends); after `DbConnPool.close()` the
  marker exists and the PID is gone (`os.kill(pid, 0)` raises).
- **E2E, exit path** (`test_server_lifecycle.py`): start the MCP over
  stdio with a long-running script that writes its PID; close stdin;
  assert the PID is gone within `_TERMINATE_GRACE_S` + 2 s. Second
  case: same with `--transport streamable-http`, stopped with
  `SIGTERM`.
- **Windows unit** (`tests/unit/sql/test_win_job.py`,
  `skipif os.name != "nt"`): `WindowsJob` kills a child and a
  grandchild (`python -c` spawning `python -c "sleep"`).
- **Windows manual** (on the Windows test machine over SSH; needs
  Python and `uv` installed there first): quoted path with a space
  under `C:\Users\…` starts; launcher-started script whose child
  binds a port — after teardown the port is free and a second start
  succeeds; closing the client leaves no process from the tree; a
  run-and-exit script that starts a detached background process and
  exits 0 leaves that process running; the PRD's "uv children
  survive `TerminateProcess`" question is answered as a side result.

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
| FR-9: quote-aware splitting | `auto-test` | unit | `test_split_command.py` passes for both rule sets; startup-validation tests (flag + env) exit 2 without echoing the value |
| FR-9: Windows spawn of quoted path | `manual-run-claude` | Windows | SSH session log: script under a path with a space starts and emits READY |
| FR-10: POSIX graceful teardown | `auto-test` | unit + integration | teardown unit cases pass; integration marker file written by the `SIGTERM` trap |
| FR-11: Windows tree teardown | `auto-test` + `manual-run-claude` | Windows | `test_win_job.py` passes on Windows; port free and second start succeeds after teardown |
| FR-12: teardown on MCP exit | `auto-test` + `manual-run-claude` | e2e + Windows | `test_server_lifecycle.py` stdin-close case passes; Windows: no process from the tree after closing the client |
| NFR-6: Windows verification | `manual-run-claude` | Windows | commands and output recorded in the task list |

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

### Amendment: Windows command splitting (FR-9)

- **`shlex.split` (POSIX mode) on every OS** — rejected: treats `\`
  as an escape and destroys `C:\…` paths.
- **`shlex.split(posix=False)` + strip outer quotes** — rejected:
  keeps quotes inside tokens (`a"b c"` → `a"b c"`) and needs ad-hoc
  post-processing; behaviour of `""` is surprising.
- **`CommandLineToArgvW` (via ctypes) or a port of its rules** —
  rejected: under those rules `\"` escapes a quote, so the common
  `"C:\dir\"` becomes `C:\dir"`; the ctypes form also only runs on
  Windows, so the main tests could not run on macOS.
- **Small no-escape tokenizer (chosen)**: quotes group, backslash is
  literal; covers every Windows path; pure Python, tested on every
  OS. Cost: no way to put a literal `"` in an argument.

### Amendment: Windows tree kill (FR-11) — user decision 2026-10-08

- **`taskkill /T /F /PID`** — rejected: walks parent-PID links, so
  grandchildren whose parent already exited are missed, and it does
  nothing when the MCP itself is force-killed.
- **`CTRL_BREAK_EVENT` to a new process group** — rejected: the PRD
  defines no graceful step on Windows; it needs script cooperation
  and a shared console.
- **Job object with kill-on-close via ctypes (chosen)**: kills the
  whole tree including re-parented descendants, and also covers MCP
  crash / force-kill. Cost: ~70 lines of Windows-only ctypes, an
  attach race of a few event-loop iterations, and mode-dependent job
  release so run-and-exit background processes survive.
- **pywin32** — rejected: new runtime dependency for ~5 calls.

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
- The script command string is split by `split_command()` (FR-9),
  in `_spawn()` and once at startup in `server.main()`. Values with
  none of `'`, `"`, `\` split exactly as `str.split()` did, which
  keeps PATH lookup and every existing test fixture unchanged.
- Only `tests/unit/sql/test_connection_script.py` assertions that
  `kill()` is the first or only teardown step may change (PRD
  constraint); all other existing tests stay unmodified.
- `win_job.py` must not be imported on POSIX (`ctypes.windll` does
  not exist there); the import sits inside an `os.name == "nt"`
  branch.

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

### Create / Modify (amendment 2026-10-08)

- Create `src/postgres_mcp/sql/win_job.py` — `WindowsJob`.
- Create `tests/unit/sql/test_split_command.py`,
  `tests/unit/sql/test_win_job.py` (Windows-only).
- Modify `src/postgres_mcp/sql/connection_script.py` — add
  `split_command()`, `_TERMINATE_GRACE_S`; `_spawn()` uses
  `split_command()` and attaches the job on Windows; `_teardown()`
  per §Teardown.
- Modify `src/postgres_mcp/server.py` — `--help` text for
  `--pre-connect-script` states the quoting rules; startup
  validation via `parser.error`; signal handler cancels `main`;
  `try/finally` around the transport as the single teardown owner.
- `connection_script.py` `_spawn()`: `stderr` inherited instead of
  `PIPE`.
- Modify `tests/unit/sql/test_connection_script.py`,
  `tests/integration/test_pre_connect.py`,
  `tests/e2e/test_server_lifecycle.py`, and the test file that drives
  `main()` — per §Amendment tests.
- Modify `README.md` (quoting rules per OS, Windows teardown
  behaviour) and `CHANGELOG.md` (POSIX values containing `'`, `"`,
  `\` now interpreted).

### Not Modified

- `src/postgres_mcp/config.py` — `ReconnectConfig` schema unchanged.
- `src/postgres_mcp/event_store.py` — no new categories, no new fields.
- All existing unit tests for `_run_pre_connect_hook` (the test file
  remains; its tests now exercise the same behaviors via the new
  manager — see Testing Strategy).

## Dependencies

### External

None. `asyncio.subprocess`, `asyncio.Event`, and standard library
regex are sufficient. No new pip dependency. The amendment adds only
stdlib `shlex` and `ctypes` (Windows `kernel32`).

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
- **Subprocess injection**: `pre_connect_script` is split into argv
  and executed without a shell (`create_subprocess_exec`), on both
  rule sets; quote handling adds no shell interpretation (no
  variables, globbing, or pipes). Whoever sets the config already
  chooses the program; no new attack surface.
- **Secrets in the command value**: the unbalanced-quote error and
  `split_command()` messages never include the value, because the
  command line may carry credentials.
- **Process leak on MCP crash**: on Windows the job object's
  kill-on-close ends the script tree when the MCP process dies for
  any reason. On POSIX, `close()` reaps the script on every normal
  exit (FR-12); an abnormal MCP exit (kill -9) still leaves the
  script orphaned, and the script is responsible for handling EOF on
  its stdout. Documented in README.

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

Amendment (2026-10-08): ships as a patch release. Rollback = revert
the amendment commit and publish the previous behaviour as a new
patch version (PyPI versions cannot be re-uploaded). Users who
started quoting paths would see quotes passed literally again; the
downstream installer keeps its version pin on the fixed release, so
it is not affected by a later rollback release.

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
- `src/postgres_mcp/sql/connection_script.py:198-215, 318-338` —
  `_spawn()` and `_teardown()` (amendment).
- `src/postgres_mcp/server.py:700, 747-795` — option help, signal
  wiring, transport run, `shutdown()` (amendment).
- `tests/unit/sql/test_connection_script.py:95-102, 318-333` —
  `FakeProcess` and the timeout-kill test (amendment).

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
3. Amendment 2026-10-08: run `/embo:tasks` to add user stories for
   (a) `split_command` + startup validation + docs, (b) POSIX graceful
   teardown, (c) `WindowsJob` + Windows teardown, (d) exit-path
   teardown, (e) Windows verification on the test machine,
   (f) release and reply to the downstream project.
