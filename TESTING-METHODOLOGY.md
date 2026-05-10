# Testing Methodology

This is a **fault-injection catalogue**. For each failure mode the
server must survive, it documents what we break, how we break it, and
what we assert. For the architecture under test, see
[`ARCHITECTURE.md`](./ARCHITECTURE.md).

Three layers of fault injection, increasing in fidelity:

| Layer | Faults induced via | Realism |
|---|---|---|
| **Unit** | `FakeProcess` (drives stdout + exit), `patch("asyncio.create_subprocess_exec")` | Pure logic — no real subprocesses, no real network |
| **Integration** | `pg_terminate_backend()` against k8s Postgres, real driver | Real libpq disconnects, real reconnect timing |
| **E2E local** | Real MCP subprocess + `os.kill(pid, SIGTERM)` on the script, `tmp_path`-mutated fixture scripts, port-1 dummy URLs | Real process tree, real signals, no AWS |
| **E2E SSM** | `kill_tunnel()` of `aws ssm start-session`, `pgrep -P` + `os.kill(pid, 9)` of SSM child, `aws ssm send-command "docker compose stop\|start\|restart postgres"` | Live AWS infrastructure |

---

## Connection stability — what we break

### 1. The pre-connect script silently hangs

**Fault:** spawn a fake subprocess that emits nothing and never exits.
**Where:** `unit/sql/test_connection_script.py::TestHookTimeout::test_no_output_no_exit_times_out_and_kills_process`
**How:** `FakeProcess` with no `feed_line()` and no `set_exit_code()`; `ensure_ready()` called with a short `hook_timeout`.
**Assert:** `ScriptOutcome(success=False, error="ready timeout")`; the fake process received `kill()`; no orphaned reader tasks (verified via pytest's warning capture).

### 2. The pre-connect script exits with a non-zero code before READY

**Fault:** fake subprocess immediately exits with code 1.
**Where:** `unit/sql/test_connection_script.py::TestRunAndExitMode::test_exit_nonzero_before_ready_returns_failure`
**How:** `factory.next = lambda fp: fp.set_exit_code(1)` then `patch("asyncio.create_subprocess_exec", fake_exec)`.
**Assert:** `ScriptOutcome(success=False, mode=RUN_AND_EXIT)`. Pool transitions to `ERROR`, doesn't crash.

### 3. The script emits a malformed `[MCP] DB_URL` line

**Faults (two layers):**

| Layer | Where | Mechanic |
|---|---|---|
| Unit | `unit/sql/test_connection_script.py::TestProtocolGrammar::test_malformed_db_url_emits_warning_and_keeps_prior_override` | `FakeProcess.feed_line("[MCP] DB_URL not-a-url")` between a valid override and `READY_TO_CONNECT` |
| E2E | `e2e/test_long_running_script.py::test_malformed_db_url_falls_back_to_configured_url` | `tmp_path`-written bash fixture: `printf '[MCP] DB_URL not-a-valid-url\n[MCP] READY_TO_CONNECT\n'; exec sleep 2147483647` |

**Assert:** warning event recorded (`"DB_URL malformed"` substring present in the EventStore), prior override retained (unit) or MCP falls back to its configured `--database-url` (E2E), no crash, `SELECT 1` succeeds.

### 4. The script emits an unknown `[MCP]` keyword

**Fault:** repeated `[MCP] FOO 1`, `[MCP] FOO 2`, `[MCP] FOO 3` lines.
**Where:** `unit/sql/test_connection_script.py::TestProtocolGrammar::test_unknown_keyword_warning_is_rate_limited_per_keyword`
**How:** `FakeProcess.feed_line()` x3 with the same unknown keyword, then `READY_TO_CONNECT`.
**Assert:** exactly **one** warning event per keyword (rate-limited), script still classified as long-running, no crash.

### 5. The long-running script dies mid-session

**Faults (three layers):**

| Layer | Where | Mechanic |
|---|---|---|
| Unit | `unit/sql/test_connection_script.py::TestProactiveDisconnectWatcher::test_long_running_script_exit_marks_pool_invalid_within_one_second` | `FakeProcess.set_exit_code(-15)` after pool is established |
| E2E local | `e2e/test_long_running_script.py::test_script_exit_marks_connection_invalid_within_one_second` | Discover the script PID from the `"started ... pid=N"` event in the EventStore, then `os.kill(pid, signal.SIGTERM)` |
| E2E SSM | `e2e/test_ssm_disruption.py::TestLongRunningSsmTunnelKill::test_reconnect_after_ssm_child_kill` | `pgrep -P <script_pid>` to find the `aws ssm start-session` child, then `os.kill(child_pid, 9)` — the script's `wait` returns → script exits → watcher fires |

**Assert (timing contract):** `t0 = time.monotonic()` taken at the kill; `mark_invalid()` reflected in `status` within **< 1 second** (unit, asserted via `monotonic()` delta; E2E budget is wider to absorb tool round-trips). Then `execute_sql` recovers without a server restart.

### 6. The SSM tunnel is killed (run-and-exit script flow)

**Fault:** kill the `aws ssm start-session` subprocess that owns the local port-forward.
**Where:** `e2e/test_ssm_disruption.py::TestTunnelKill::test_reconnect_after_tunnel_kill`
**How:** `kill_tunnel(initial_tunnel)` (`SIGTERM` then `SIGKILL` if needed) on the live AWS-spawned `aws ssm start-session` process. `--reconnect-initial-delay 1 --reconnect-max-delay 10 --reconnect-max-attempts 15` to bound the wait.
**Assert:** the next `execute_sql` either errors or succeeds; after up to 5s of backoff, a subsequent `execute_sql` returns `2 AS after_kill`; `status.state == "connected"`.

### 7. Every backend connection is terminated server-side

**Faults (three layers):**

| Layer | Where | Mechanic |
|---|---|---|
| Integration | `integration/test_reconnect.py::TestReconnectAfterTerminate::test_query_after_terminate_triggers_reconnect` | Open a side connection, `SELECT pg_backend_pid()` from the pool, then `SELECT pg_terminate_backend(pid)` from the side connection |
| Integration | `integration/test_reconnect.py::test_data_integrity_after_reconnect` | Same, with an `INSERT 'before_kill'` before the kill and an `INSERT 'after_kill'` after; assert both rows visible |
| E2E SSM | `e2e/test_ssm_disruption.py::TestConnectionKillViaSql::test_recover_after_all_backends_killed` | Open a *killer* `psycopg.AsyncConnection`, enumerate every `mcp_reader` backend via `pg_stat_activity`, `pg_terminate_backend()` each |

**Assert:** the pool transparently reconnects on the next query; `reconnect_count` increments in `status.metadata`; data inserted pre-kill remains visible post-reconnect (integration).

### 8. Postgres is stopped, then started

**Fault:** stop the PG container on the remote EC2 instance.
**Where:** `e2e/test_ssm_disruption.py::TestPgServiceStopStart::test_recover_after_pg_stop_start`
**How:** `aws ssm send-command "cd $REMOTE_PROJECT_DIR && docker compose stop postgres"`, wait 5s, attempt 8 queries on 2s intervals — assert at least one errors. Then `docker compose start postgres`, wait 15s, assert `SELECT 'recovered'` succeeds.
**Assert:** pool detects the outage (at least one error during stop), then recovers automatically once PG is back. No server restart.

### 9. Postgres is restarted (single command)

**Fault:** `docker compose restart postgres` on the remote.
**Where:** `e2e/test_ssm_disruption.py::TestPgServiceRestart::test_recover_after_pg_restart`
**How:** `aws ssm send-command`, wait 10s, query — if error, wait 5s, retry once.
**Assert:** within one retry, `SELECT 'back'` succeeds.

### 10. Server started against an unreachable host

**Fault:** `--database-url postgresql://nobody:wrong@127.0.0.1:1/nope` (port 1 is always closed).
**Where:** `e2e/test_server_lifecycle.py::TestBadConnectionString::test_server_stays_alive_with_unreachable_host`
**How:** boot a real `fluid-postgres-mcp` subprocess via `McpSession` with the dead URL.
**Assert:** process stays alive; `status` is queryable; `execute_sql` returns an error to the client (not a server crash).

### 11. Server receives SIGTERM mid-operation

**Fault:** `proc.send_signal(signal.SIGTERM)` on the running MCP subprocess.
**Where:** `e2e/test_server_lifecycle.py::TestGracefulShutdown::test_sigterm_exits_cleanly`
**How:** start the server, `time.sleep(3)`, send SIGTERM, wait up to 10s for exit.
**Assert:** clean exit within 10s; no orphaned child processes (this is what catches reaper bugs in the script manager).

---

## Reconnection — what we break

### 12. The pre-connect script's emitted `DB_URL` changes between invocations

**Fault:** swap the URL the script will emit after it gets killed.
**Where:** `e2e/test_long_running_script.py::test_url_rotation_across_script_respawn`
**How:** `tmp_path / "current_url"` holds the URL; the fixture bash script reads it on each invocation. Test:
1. Write URL_A to the file → start MCP → first script reads URL_A → connect.
2. Capture script PID from the EventStore.
3. Overwrite the file with URL_B.
4. `os.kill(pid_a, SIGTERM)` → watcher fires → script respawns → reads URL_B.

**Assert:** next `execute_sql` succeeds via URL_B; the EventStore contains two distinct `DB_URL` events.

### 13. SSM Parameter Store rotates the DB password between script invocations

**Fault:** swap the password the script will fetch.
**Where:** `e2e/test_ssm_disruption.py::TestLongRunningSsmCredentialRotation::test_second_invocation_emits_fresh_db_url`
**How:** tmp-file wrapper that lets the test mutate the password source without touching the real Parameter Store. Force disconnect, observe respawn.
**Assert:** the second pool is created with the new URL; `status.events` contains ≥2 distinct `DB_URL` events across the rotation.

### 14. Reconnect must exhaust its retry budget, not spin forever

**Fault:** force every reconnect attempt to fail.
**Where:** `unit/sql/test_reconnect.py::TestReconnectLoop::test_max_attempts_exhaustion`
**How:** unit-level — backoff is driven by a clock fake so we don't sleep real seconds; pool's create-pool call is mocked to always raise.
**Assert:** after `max_attempts` failures, state is `ERROR` (terminal until next `ensure_connected()`); the loop exits rather than spinning. Exponential backoff timing is asserted in a sibling test (`test_exponential_backoff_timing`) via the same clock fake.

### 15. Concurrent `ensure_ready()` calls (no double-spawn)

**Fault:** two coroutines call `ensure_ready()` simultaneously.
**Where:** `unit/sql/test_connection_script.py::TestSerialisationAndStop::test_concurrent_ensure_ready_does_not_double_spawn`
**How:** `asyncio.gather(mgr.ensure_ready(), mgr.ensure_ready())` against a `FakeProcess` that counts spawns.
**Assert:** the underlying `asyncio.create_subprocess_exec` mock was called exactly once; both callers receive the same `ScriptOutcome` via the manager's `_inflight` future.

---

## Visibility — what we break, what we observe

The `status` tool is itself the assertion target for most stability
faults above. This section covers visibility-specific faults: the
event store under stress and credential leak prevention.

### 16. Event buffer overflow (more events than buffer size)

**Fault:** record more events into one category than the buffer holds.
**Where:** `unit/test_event_store.py::TestRingBuffer::test_wraps_when_full`
**How:** construct an `EventStore` with a small buffer, record N+5 events.
**Assert:** the last N are present; the first 5 are gone; order preserved.

### 17. Credential leak surface — pre-connect script emits a URL with a password

**Faults (three layers):**

| Layer | Where | Mechanic |
|---|---|---|
| Unit | `unit/sql/test_connection_script.py::TestEventCatalog::test_no_password_substring_appears_in_any_event` | `FakeProcess.feed_line("[MCP] DB_URL postgresql://u:SUPERSECRET@h/db")`, capture every `on_event` callback message |
| Unit | `unit/test_status_tool.py::TestStatusNoCredentials::test_no_connection_string_in_output` | Construct a pool with a password-bearing URL, call `status` directly |
| Integration | `integration/test_status.py::test_no_credentials_in_output` | Same against the real driver — covers fields the unit layer can't, e.g. interpolated libpq error messages |
| E2E | `e2e/test_mcp_status.py::TestStatusNoCredentials::test_no_password_in_status_output` | Same surface through the MCP stdio client — what users actually see |

**Assert (all four):** the password substring (`"SUPERSECRET"`, or whatever the test used) appears in **zero** event messages, **zero** status fields, **zero** logs.

### 18. Concurrent disconnect + restart events (visibility under stress)

**Fault:** drive a full lifecycle through the EventStore in quick succession.
**Where:** `integration/test_status.py::test_full_sequence`
**How:** connect → run a query → `pg_terminate_backend` → wait for reconnect → query again.
**Assert:** `status.events` contains the entire sequence in chronological order: connect → query → disconnect → reconnect. Nothing dropped, nothing out of order.

### 19. Reconnect count metadata under repeated kills

**Fault:** kill backends repeatedly and watch the counter.
**Where:** `integration/test_reconnect.py::test_reconnect_count_increments`
**How:** loop N times: `mark_invalid()` → wait for reconnect → assert counter incremented.
**Assert:** `metadata.reconnect_count` is monotonically increasing; visible via `status` after each round.

---

## Test inventory by fault target

A flat index — for when you want to know "is there a test for X?"

| Target | Tests |
|---|---|
| Script hangs silently | `TestHookTimeout` |
| Script exits non-zero pre-READY | `TestRunAndExitMode::test_exit_nonzero_before_ready_returns_failure` |
| Malformed `DB_URL` | `TestProtocolGrammar::test_malformed_db_url_*`, `test_malformed_db_url_falls_back_to_configured_url` |
| Unknown `[MCP]` keyword | `TestProtocolGrammar::test_unknown_keyword_*` |
| Long-running script dies | `TestProactiveDisconnectWatcher::*`, `test_script_exit_marks_connection_invalid_within_one_second`, `TestLongRunningSsmTunnelKill::test_reconnect_after_ssm_child_kill` |
| SSM tunnel killed | `TestTunnelKill::test_reconnect_after_tunnel_kill` |
| Backend terminated | `TestReconnectAfterTerminate::*`, `TestConnectionKillViaSql::test_recover_after_all_backends_killed` |
| PG service stop/start | `TestPgServiceStopStart::test_recover_after_pg_stop_start` |
| PG restart | `TestPgServiceRestart::test_recover_after_pg_restart` |
| Unreachable host on boot | `TestBadConnectionString::*` |
| SIGTERM handling | `TestGracefulShutdown::test_sigterm_exits_cleanly` |
| URL rotation across respawn | `test_url_rotation_across_script_respawn` |
| Password rotation via Parameter Store | `TestLongRunningSsmCredentialRotation::test_second_invocation_emits_fresh_db_url` |
| Retry-budget exhaustion | `TestReconnectLoop::test_max_attempts_exhaustion`, `test_exponential_backoff_timing` |
| Concurrent `ensure_ready` | `TestSerialisationAndStop::test_concurrent_ensure_ready_does_not_double_spawn` |
| Event buffer overflow | `TestRingBuffer::test_wraps_when_full` |
| Password leak in events / status | `TestEventCatalog::test_no_password_substring_appears_in_any_event`, `TestStatusNoCredentials::*` (3 layers), `test_no_credentials_in_output` |
| Full lifecycle visibility | `integration/test_status.py::test_full_sequence` |
| Reconnect count visibility | `test_reconnect_count_increments`, `test_metadata_with_reconnect_count` |

## Authoring new fault tests

- **Subprocess faults at unit layer** → `FakeProcess` from
  `tests/unit/sql/test_connection_script.py` (drives stdout via
  `feed_line()`, exit via `set_exit_code()`). Patch
  `asyncio.create_subprocess_exec` to inject it.
- **Timing assertions** → `time.monotonic()` delta around the trigger;
  don't poll, don't sleep-then-check. The `<1s` watcher contract is
  asserted this way.
- **E2E subprocess kills** → discover the target PID from the
  EventStore (`"started ... pid=N"` event), then `os.kill(pid, sig)`.
  Use `McpSession` from `tests/e2e/mcp_client_fixtures.py` for the
  client side — raw `stdio_client + ClientSession` triggers anyio
  cancel-scope errors on shutdown.
- **Fixture scripts that need to survive SIGTERM cleanly** →
  `exec sleep 2147483647`. The bash `trap`+`wait` pattern is
  unreliable on macOS — `proc.wait()` does not see SIGCHLD through it.
- **Mutating fixture scripts mid-test** → write the script to
  `tmp_path` and have the bash read state from a sibling file the
  test mutates between invocations. See
  `test_url_rotation_across_script_respawn` for the pattern.
- **SSM remote command faults** → `ssm_send_command(config, env, "...")`
  in `tests/e2e/ssm_fixtures.py`. Skips automatically if the analyst
  role lacks `ssm:SendCommand`.
