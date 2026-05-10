# 002-connection-tunnel-script: Long-Running Pre-Connect Script — PRD

**Status**: Draft
**Created**: 2026-05-10
**Author**: Claude (via dev workflow)

---

## Context

The MCP currently supports a `--pre-connect-script` that runs in
**run-and-exit mode**: spawn → wait for exit → check exit code → if 0,
attempt psycopg connect. Disconnection is detected only reactively,
when a query fails with `psycopg.OperationalError`. The script is
re-spawned on each reconnect attempt with exponential backoff.

This works for setups where the tunnel is self-healing externally
(systemd, autossh, a Docker sidecar) and the script's role is only to
*verify* the tunnel is up. It does not work well for setups where the
script *is* the tunnel — typified by `aws ssm start-session`
port-forwarding for analyst access to the CRM Postgres instance:

- A dead tunnel goes unnoticed until the next user query, then the
  user waits through a backoff loop with no warning.
- The script has no way to tell the MCP that the connection URL has
  changed (e.g. local port re-picked, password rotated).
- The MCP cannot distinguish "tunnel still healthy, waiting for query"
  from "tunnel died ten minutes ago."

This PRD introduces a second mode — **long-running mode** — where the
same `--pre-connect-script` flag points at a script that owns the
tunnel for the lifetime of the MCP, communicates with the MCP via a
strict line-prefixed stdout protocol, and exits only when the tunnel
breaks. Mode is auto-detected from the script's behavior; existing
run-and-exit users are unaffected.

### Current State (observed)

- `--pre-connect-script` is implemented as
  `DbConnPool._run_pre_connect_hook()` which calls
  `asyncio.create_subprocess_exec(*script.split(), ...)`,
  awaits `proc.communicate()` with timeout `hook_timeout`, and returns
  `bool` based on exit code — verified via:
  `src/postgres_mcp/sql/sql_driver.py:115-143`, 2026-05-10
- The script is invoked from `pool_connect()` (initial connect) and
  `_reconnect_loop()` (every reconnect attempt) — verified via:
  `src/postgres_mcp/sql/sql_driver.py:170, 214`, 2026-05-10
- Stdout/stderr are captured into byte strings and dumped via
  `logger.debug()`; not parsed for any content — verified via:
  `src/postgres_mcp/sql/sql_driver.py:137-139`, 2026-05-10
- Disconnection detection is reactive: `SqlDriver.execute_query()`
  catches `psycopg.OperationalError`, calls `mark_invalid()`, the
  next call to `ensure_connected()` triggers `_reconnect_loop()` —
  [assumption, verify in tech-design]
- `hook_timeout` defaults to 10 seconds in `ReconnectConfig` and is
  exposed as `--hook-timeout` — verified via:
  `src/postgres_mcp/config.py` and `--hook-timeout` argparse
  registration in `src/postgres_mcp/server.py:664`, 2026-05-10
- `EventStore` is wired into `DbConnPool` via the `_emit()` callback;
  every connection state change emits an event with
  `obfuscate_password()` applied — verified via:
  `src/postgres_mcp/sql/sql_driver.py:111-113, 142, 227, 237`,
  2026-05-10
- The `status` MCP tool exposes EventStore contents to clients —
  verified via task 001 user story 7.0 acceptance,
  `tasks/001-mcp-initial/2026-05-08-001-mcp-initial-tasks.md:273-305`

### Decisions Already Made

- **Same flag**: Mode is detected from script behavior; no new flag.
- **Backwards compatible**: Existing run-and-exit scripts (including
  the 11.x SSM E2E test fixtures) continue to work unchanged.
- **Protocol shape**: Strict line prefix + keyword. Lines starting
  with the protocol prefix are interpreted; everything else is
  diagnostic output drained to debug log.
- **`hook_timeout` semantics in long-running mode** (Option B):
  applied to *every* wait for `READY_TO_CONNECT`, both initial launch
  and after a reconnect is needed. Catches deadlocks without new
  config.
- **Pool invalidation on script exit**: When the script exits
  unexpectedly, MCP marks the pool invalid immediately (does not wait
  for next query to fail), restarts the script, waits for next
  `READY_TO_CONNECT`, attempts fresh connect. Spurious reconnects on
  script crashes are accepted as the cost of instant detection.
- **Idempotent script behavior**: When restarted, the script must
  cope gracefully with a tunnel still running on the same port from a
  previous incarnation (e.g. `nc -z` short-circuit, or kill + re-open).
  This constraint is on the script author, not the MCP.

### Past Similar Features (from claude-mem)

- `tests/e2e/test_ssm_disruption.py` (claude-mem #13055, 2026-05-09)
  proves the run-and-exit mode works end-to-end against real
  EC2/SSM infrastructure — tunnel kill, `pg_terminate_backend`,
  Docker stop/start/restart all recover. Long-running mode must
  preserve all of this coverage and add proactive detection.

## Problem Statement

**Who**: AI agents using `fluid-postgres-mcp` against a PostgreSQL
that requires a script-managed tunnel (SSM port-forward, SSH tunnel,
WireGuard sidecar) for reachability.

**What**:
1. The script cannot tell the MCP what the runtime connection URL
   should be. Today the URL must be hardcoded at MCP-launch time,
   which forces secrets-with-rotation and dynamically-allocated ports
   to be resolved before launch and frozen for the session.
2. The MCP cannot detect tunnel death until a user query fails.
   Long-idle sessions accumulate undetected outages.
3. The script has no way to declare "I am ready" beyond exiting; this
   conflates "I succeeded and exited" with "I succeeded and stayed
   alive to keep the tunnel open" — only the former is supported.

**Why**:
1. Forces analyst tooling to leak credentials into MCP-client config
   files (`claude.json`) or to launch the MCP through a wrapper
   script — defeating the design goal of `claude mcp add fluid-postgres-mcp …`.
2. Causes UX delays measured in seconds-to-minutes between an outage
   and the user noticing — silent failures during idle periods.
3. Makes the script protocol fragile: a script that wants to keep the
   tunnel open must `disown` background processes and exit fast,
   which is operationally awkward and hides real failures.

**When**: Production analyst sessions lasting hours, against
PostgreSQL instances reachable only through script-managed tunnels.
Particularly acute during credential rotation events and tunnel
restarts.

## Goals

### Primary Goal

Allow `--pre-connect-script` to run in **long-running mode**: the
script owns the tunnel for the lifetime of the MCP, communicates
state changes via stdout protocol lines, and signals readiness for
the MCP to attempt psycopg connect. The MCP detects tunnel death
the instant the script exits, not the next time a query fails.

### Secondary Goals

- Preserve full backwards compatibility with run-and-exit mode
  (including the existing 11.x SSM E2E test fixtures).
- Eliminate the need to embed PostgreSQL credentials into MCP-client
  configuration files; the script can resolve them at runtime and
  pass them to the MCP via a stdout protocol line.
- Make all connection-state changes — including script lifecycle
  events — visible via the existing `status` tool, without silent
  hangs or ambiguous messages.
- Expand E2E coverage to validate proactive disconnection detection,
  not just reactive recovery.

## User Stories

### Epic

As an AI agent owner deploying `fluid-postgres-mcp` against a
PostgreSQL instance reachable only via a script-managed tunnel,
I want the pre-connect-script to run for the lifetime of the MCP and
declaratively communicate connection readiness, so that no secrets
live in client config files, tunnel death is detected instantly, and
my analytical sessions survive credential rotation transparently.

### User Stories

1. **As an analyst,**
   **I want** the MCP to start serving queries within seconds of
   launch even when the connection URL is unknown until the tunnel is
   open
   **So that** I can register the MCP with `claude mcp add` without
   ever pasting a database URL or password.

   **Acceptance Criteria**:
   - [ ] MCP can be launched with no `DATABASE_URI` and no positional
         URL argument, only a `--pre-connect-script` pointing at a
         long-running script.
   - [ ] The MCP successfully connects when the script emits a
         `DB_URL:` line followed by `READY_TO_CONNECT`.
   - [ ] If the MCP is launched with both a `DATABASE_URI` and a
         long-running script that emits `DB_URL:`, the script's URL
         takes precedence.
   - [ ] If the script never emits `DB_URL:` but emits
         `READY_TO_CONNECT`, the pre-configured URL is used.

2. **As an analyst running an idle session,**
   **I want** the MCP to detect a tunnel-death event within 1 second
   of the tunnel breaking
   **So that** when I issue my next query, the connection is already
   re-established or actively reconnecting, not waiting for my query
   to discover the outage.

   **Acceptance Criteria**:
   - [ ] When the long-running script exits while the pool is
         healthy, MCP marks the pool invalid within 1 second of
         script exit (measured from process exit to EventStore
         emission).
   - [ ] MCP automatically restarts the script and waits for the
         next `READY_TO_CONNECT` before attempting reconnect.
   - [ ] The `status` tool reflects the disconnect within 1 second of
         the script exit, with an event explaining the cause
         (script exit code, last protocol-line activity).

3. **As an analyst whose database password rotates mid-session,**
   **I want** the script to be able to deliver a new URL on
   reconnect
   **So that** my session survives the rotation without restart.

   **Acceptance Criteria**:
   - [ ] When the script emits a new `DB_URL:` line followed by
         `READY_TO_CONNECT` after a forced disconnect, the MCP uses
         the new URL for the next connect attempt.
   - [ ] The new URL replaces the previous URL for all subsequent
         reconnect attempts in the same session, not only the
         immediate one.

4. **As an MCP operator,**
   **I want** every script lifecycle event surfaced via the `status`
   tool
   **So that** when an analyst reports "MCP is hanging," I can
   diagnose remotely without shelling onto the box.

   **Acceptance Criteria**:
   - [ ] EventStore records: script started, mode detected
         (run-and-exit vs long-running), `READY_TO_CONNECT` received,
         `DB_URL:` received (URL obfuscated), script exited (with
         code), script restarted, connect attempted, connect failed
         (cause), pool invalidated.
   - [ ] No state transition occurs without an EventStore record.
   - [ ] All events containing connection-string fragments pass
         through `obfuscate_password()`.

5. **As a maintainer of an existing run-and-exit script** (e.g.
   `tests/e2e/ssm_fixtures.py::create_tunnel_script`),
   **I want** my script to keep working without modification
   **So that** the long-running mode is a strict capability addition,
   not a migration burden.

   **Acceptance Criteria**:
   - [ ] All 11.x SSM E2E tests pass without changes to the test
         fixtures or scripts.
   - [ ] All existing unit tests for `_run_pre_connect_hook()` pass
         without changes to test code.

6. **As an MCP operator,**
   **I want** a deadlocked or hung long-running script to be killed
   and restarted, not left to wedge the MCP
   **So that** a script bug never produces silent unresponsiveness.

   **Acceptance Criteria**:
   - [ ] If a script in long-running mode does not emit
         `READY_TO_CONNECT` within `hook_timeout` of any (re)connect
         wait, MCP kills the script, records an event, and enters
         backoff before retry.
   - [ ] The MCP never blocks indefinitely on script behavior. Every
         wait state has either a timeout or a definitive signal
         (process exit, line received).

## Requirements

### Functional Requirements

1. **FR-1: Long-running mode auto-detection**
   - **Priority**: High
   - **Rationale**: User-stated requirement that no new flag is
     introduced; the mode is observed from the script's behavior.
   - **Behavior**: On script launch, MCP waits up to `hook_timeout`
     for whichever happens first: process exit (run-and-exit mode,
     proceed by exit code), or a `READY_TO_CONNECT` protocol line
     (long-running mode, leave script running). If neither happens
     before timeout, MCP kills the script and treats it as a hook
     failure.

2. **FR-2: Strict line-prefixed stdout protocol**
   - **Priority**: High
   - **Rationale**: Clear separation between protocol lines and
     diagnostic output; minimal collision risk.
   - **Behavior**: Lines on the script's stdout matching a fixed
     prefix-and-keyword grammar are interpreted as protocol events.
     Two events are defined for v1: a "set connection URL" event
     carrying a postgresql:// URL payload, and a "ready to connect"
     event carrying no payload. Every other line is treated as
     diagnostic output and drained to the MCP's debug log without
     interpretation. The exact prefix and grammar are an
     implementation detail of tech-design; the prefix is a
     short, fixed token chosen to be highly unlikely to collide with
     normal shell-script diagnostic output.

3. **FR-3: Connection URL override**
   - **Priority**: High
   - **Rationale**: Lets the script resolve the URL at runtime
     (post-tunnel-open, post-credential-fetch) and update it on
     rotation.
   - **Behavior**: When MCP receives a "set connection URL" event,
     the URL replaces the connection URL used for the *next* connect
     attempt. The override persists for the rest of the session
     until another such event arrives. Malformed URLs (rejected by
     the URL parser) are logged as warnings; the previous URL is
     retained.

4. **FR-4: Connection trigger via `READY_TO_CONNECT`**
   - **Priority**: High
   - **Rationale**: Decouples "script is alive" from "script is
     ready"; lets the script perform multi-step setup (assume role,
     open tunnel, wait for port to listen) before signalling.
   - **Behavior**: In long-running mode, MCP attempts psycopg
     connect only after a `READY_TO_CONNECT` line is received. If a
     `DB_URL:` line was received in the same window, that URL is
     used; otherwise the pre-configured URL.

5. **FR-5: Instant disconnect detection on script exit**
   - **Priority**: High
   - **Rationale**: Eliminates the silent-rotting-tunnel UX problem.
   - **Behavior**: In long-running mode, when the script process
     exits (any exit code), MCP marks the pool invalid within 1
     second, emits an event, restarts the script, and enters the
     reconnect flow waiting for the next `READY_TO_CONNECT`. The
     existing exponential backoff applies between script restarts.

6. **FR-6: `hook_timeout` applied to every `READY_TO_CONNECT` wait**
   - **Priority**: High
   - **Rationale**: User-decided Option B. Catches deadlocks
     uniformly without adding new config.
   - **Behavior**: On both initial launch and every reconnect
     iteration in long-running mode, the wait for the next
     `READY_TO_CONNECT` is bounded by `hook_timeout`. On timeout,
     MCP kills the script, records an event, and applies backoff
     before retrying.

7. **FR-7: Backwards compatibility**
   - **Priority**: High
   - **Rationale**: Run-and-exit is the only mode that exists today
     and is exercised by the entire 11.x SSM E2E suite.
   - **Behavior**: A script that exits within `hook_timeout` without
     emitting any protocol lines is treated as run-and-exit. The
     existing run-and-exit semantics (exit code 0 → connect; non-zero
     → backoff and retry) are preserved unchanged.

8. **FR-8: Full EventStore coverage**
   - **Priority**: High
   - **Rationale**: User requirement: "no silent hangs or ambiguous
     messages."
   - **Behavior**: Every script and connection lifecycle event
     produces exactly one EventStore record with category
     `CONNECTION` (or a new `SCRIPT` category — tech-design call).
     All records pass through `obfuscate_password()` before storage.

### Non-Functional Requirements

1. **NFR-1: Detection latency**
   - Tunnel-death-to-MCP-recognition: ≤ 1 second p99 in long-running
     mode (measured: process exit → EventStore record).

2. **NFR-2: No silent hangs**
   - Every blocking wait in MCP code paths interacting with the
     script must be bounded by either `hook_timeout` or a definitive
     signal (process exit, line received). No `await
     proc.stdout.readline()` without a parallel timeout or process-exit
     watcher.

3. **NFR-3: Output volume safety**
   - Long-running mode may consume the script's stdout for hours or
     days. Memory used to track non-protocol stdout output must be
     bounded (debug log can rotate or truncate; in-process buffers
     must be small/ring-buffered).

4. **NFR-4: Credential safety**
   - `DB_URL:` lines, when logged via the EventStore or debug logger,
     must have their password component obfuscated. The full URL is
     held in memory only for the connection attempt.

5. **NFR-5: Test coverage**
   - User explicitly called this "the core component" requiring
     "very, VERY thorough testing." Tech-design will enumerate the
     test plan; PRD-level expectation is unit + integration + E2E
     coverage of every state transition, including the new
     script-exit and `READY_TO_CONNECT`-timeout transitions, plus a
     real SSM E2E test that proactively kills the tunnel and asserts
     <1s detection.

### Technical Constraints

- **Must integrate with**: `DbConnPool._run_pre_connect_hook()`,
  `_reconnect_loop()`, `pool_connect()`, the `EventStore`, and the
  `obfuscate_password()` helper. Must not require changes to the
  `SqlDriver` query path or the `status` tool implementation.
- **Should follow patterns**: existing async-subprocess patterns
  (`asyncio.create_subprocess_exec`), existing event-emit pattern
  (`self._emit(...)`), existing `obfuscate_password()` for any
  user-visible string containing connection-string fragments.
- **Cannot change**: the `--pre-connect-script` CLI flag name, its
  argument shape, or the run-and-exit mode's existing semantics.
  All E2E tests in `tests/e2e/test_ssm_disruption.py` and unit tests
  in `tests/unit/sql/test_pre_connect_hook.py` must continue to pass
  unmodified.

## Out of Scope

- A protocol for the script to *receive* messages from the MCP
  (e.g. "please re-establish the tunnel now"). MCP-to-script signalling
  is restricted to process lifecycle (kill, restart). One-way stdout
  protocol only in v1.
- Generalised stdin protocol or RPC. The script is a child process,
  not a peer.
- Multiple concurrent pre-connect-scripts. One script, one tunnel.
- A new MCP tool for the analyst to inspect or restart the script.
  Lifecycle is fully owned by `DbConnPool`; visibility comes via the
  existing `status` tool's event log.
- Replacing the existing `pre-connect-script` flag with a new name.
- Any change to how `DATABASE_URI` env / positional arg is parsed at
  startup.
- A second protocol event for "tunnel down, please wait" before the
  script exits. Script exit *is* the tunnel-down signal in v1.

## Success Metrics

1. **Backwards compatibility regression rate**: 0 — all existing
   unit, integration, and E2E tests pass without modification.
2. **Detection latency p99**: ≤ 1 second from script process exit to
   EventStore record visible via `status` tool.
3. **New E2E coverage**: at least 4 new disruption scenarios specific
   to long-running mode (script crash, tunnel external kill, password
   rotation via new `DB_URL:`, READY timeout deadlock).
4. **No silent hangs**: 0 failures of the form "MCP became
   unresponsive without an EventStore record" across the new test
   suite.
5. **`claude mcp add` simplicity**: the analyst-onboarding flow for
   the CRM Postgres setup uses `claude mcp add fluid-postgres-mcp`
   with no `DATABASE_URI` env var or positional argument — only
   `--pre-connect-script` and timeout/reconnect flags.

## References

### From Codebase (RLM)

- `src/postgres_mcp/sql/sql_driver.py:115-143` — current
  `_run_pre_connect_hook()` implementation to extend.
- `src/postgres_mcp/sql/sql_driver.py:159-228` — `pool_connect()` and
  `_reconnect_loop()` integration points.
- `src/postgres_mcp/config.py` — `ReconnectConfig`, where any new
  config fields would live (none expected per Option B).
- `src/postgres_mcp/event_store.py` — `EventStore` and event
  categories.
- `src/postgres_mcp/server.py:626-686` — argparse and `parse_config`
  wiring; no changes expected at this layer.
- `tests/e2e/ssm_fixtures.py:222-259` — existing run-and-exit script
  template; will gain a sibling long-running variant for the new
  E2E coverage.
- `tests/e2e/test_ssm_disruption.py` — existing 11.x suite; must
  continue to pass.

### From History (Claude-Mem)

- claude-mem #13055 (2026-05-09) — E2E SSM tunnel disruption test
  suite structure.
- claude-mem #13072 (2026-05-10) — MCP server direct integration
  pattern for fluid-postgres-mcp; this PRD operationalises that
  pattern by removing the last reason a wrapper script would still
  be needed.
- Task 001 user story 4.0 — pre-connect hook with PATH lookup, exit
  code semantics, no-op when unconfigured. v2 must preserve all of
  this for run-and-exit mode.

---

**Next Steps**:
1. Review and refine this PRD.
2. Run `/dev:tech-design` to produce the protocol grammar, the
   exact `DbConnPool` state-machine extensions, error categories,
   and the test plan satisfying NFR-5.
3. Run `/dev:tasks` to break down into TDD-able subtasks.
