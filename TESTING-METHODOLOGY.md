# Testing Methodology

How we know `fluid-postgres-mcp` keeps working when things go wrong.

This document is for **users** — not test maintainers. It describes
each kind of failure we deliberately cause, and what we observe the
server actually do about it. Numbers below are real, measured against
live PostgreSQL on 2026-05-11.

For the architecture under test (state machines, event flow,
sequence diagrams), see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## Connection stability and recovery

We force the server into nine kinds of failure and check that it
keeps serving queries. Three things matter:

- **How fast does it notice the problem?** *(detection time)*
- **How long until queries succeed again?** *(recovery time)*
- **How many user queries fail in the gap?** *(lost queries)*

### Failures we cause and what happens

#### A long-running tunnel script dies mid-session

We kill the script that owns the database tunnel.

| Measurement | Result |
|---|---|
| Detection — `status` shows "Connection lost" | **44–46 ms** (3 runs) |
| Recovery — `SELECT 1` succeeds again | **2.3 s** |
| User queries lost during the gap | **0** |

The recovery time is dominated by the script respawning the tunnel
and Postgres re-accepting the connection. The first query after the
kill triggers the reconnect transparently; the user sees a small
latency bump, not an error.

#### The SSM tunnel dies (live AWS test)

We kill the `aws ssm start-session` process that holds the
port-forward open. Same shape as the script-death case above but
against real AWS infrastructure.

- The MCP detects the tunnel loss within ~1 second.
- The reconnect loop respawns the tunnel and reconnects automatically.
- `status` reports "Reconnected" — visible to the user.

#### A Postgres backend is killed server-side

Someone runs `pg_terminate_backend()` against our connection.

| Measurement | Result |
|---|---|
| Detection | on next query (libpq error) |
| Recovery — `SELECT 1` succeeds again | **3.9 s** |
| Queries that returned an error | **1** (the one that triggered detection) |
| Internal reconnect counter (visible via `status`) | incremented from 0 → 1 |

This is the "reactive" disconnect path: there's no proactive watcher
for backend termination, so the next query the user issues is the one
that gets the error. The query *after* that succeeds.

#### Every backend connection is terminated at once

We open a side connection and `pg_terminate_backend()` against every
backend the MCP pool is using. The pool transparently reconnects on
the next query; data written before the kill remains readable after.

#### Postgres is stopped and restarted

We run `docker compose stop postgres` on the host, wait 5 seconds,
then `docker compose start postgres` and wait 15 seconds.

- During the stopped window: queries return errors (PG is genuinely
  unreachable).
- After PG comes back up: `SELECT 1` succeeds again within seconds.
- No server restart needed.

#### Postgres is restarted in one motion

`docker compose restart postgres`. The pool retries with exponential
backoff and recovers within one retry attempt (typically <10 seconds).

#### The server starts pointed at an unreachable host

We launch the MCP with `postgresql://nobody:wrong@127.0.0.1:1/nope`
(port 1 is always closed) and try to use it.

| Measurement | Result |
|---|---|
| Process stays alive | **yes** |
| `status` tool queryable | **yes** |
| State reported by `status` | `error` |
| `execute_sql` returns | **an error** (not a crash) |
| Startup time (psycopg's 30 s connect timeout) | **30.7 s** |

The point: the MCP doesn't crash on an unreachable database, even at
startup. The operator can keep the session open, fix the URL or wait
for the database, and recover without restarting the MCP.

#### The server receives SIGTERM

We kill the MCP process with `SIGTERM` mid-operation. It exits
cleanly within 10 seconds. No orphaned child processes (the tunnel
script and any subprocesses are reaped).

#### The pre-connect script behaves badly

Four sub-cases:

| What the script does | What happens |
|---|---|
| Hangs silently (no output, never exits) | After `--hook-timeout` seconds, the script is killed. Startup fails cleanly with a "ready timeout" error. |
| Exits with a non-zero code before signalling ready | Connect fails with the script's exit code in the error message. Pool stays in `error` state, retryable. |
| Emits a malformed `[MCP] DB_URL` line | A warning is recorded in `status.warnings`. The MCP falls back to its configured `--database-url`. **Connection succeeds; 0 queries lost.** Measured time-to-connect: **2.6 s**. |
| Emits an unknown `[MCP]` keyword (typo in the protocol) | The keyword is logged once as a warning and ignored. The rest of the protocol is honoured normally. |

---

## Visibility — the `status` tool

When something fails, the operator should be able to ask the MCP
*"what happened?"* and get a useful answer. We test this directly:
after each fault above, we call `status` and check what comes back.

### What `status` returns (sample after a script kill)

```
{
  "state": "connected",
  "metadata": {"reconnect_count": 1, ...},
  "events": [
    "... Pre-connect-script started (pid=12345)",
    "... Connected to postgresql://reader:***@host/db",
    "... Pre-connect-script exited with code -15",
    "... Connection lost: pre-connect-script exited",
    "... Pre-connect-script started (pid=12346)",
    "... Reconnected to postgresql://reader:***@host/db"
  ],
  "warnings": [...],
  "errors": [...]
}
```

### What we verify about `status`

| Property | How we test it | Result |
|---|---|---|
| Reflects the live state | Cause a disconnect, call `status` | State changes to `error` or `reconnecting` within ~50 ms of the trigger |
| Records the full sequence in order | connect → query → disconnect → reconnect → query | All six events appear in the buffer in chronological order |
| Counts reconnects | Force N disconnects in a row | `metadata.reconnect_count` increments to N |
| Bounded buffer (no memory leak) | Push more events than the buffer holds | Oldest events drop; newest are retained; counts stay correct |
| Survives buffer-per-category | Spam one category with events | Other categories' events are untouched |

### What `status` never contains

A user pasting `status` output into a bug report shouldn't leak
credentials. We test this at four layers:

| Layer | What we check |
|---|---|
| Inside the pool | The connection URL never appears verbatim in any event message |
| Manager event payloads | `[MCP] DB_URL` events show host and database name only — the password is structurally unreachable (we parse the URL with `urllib`, not regex) |
| Driver-layer errors | libpq error messages get scrubbed via `obfuscate_password()` before being recorded |
| Across the MCP wire | We embed a recognisable password into a test URL and check that the string appears in **zero** status fields, **zero** logs, **zero** event messages |

All four layers pass: the password substring appears nowhere in the
output the user sees.

---

## Test layers

We run each fault at the cheapest layer that can express it, then
re-verify against real infrastructure:

| Layer | Faults run as | When we run it |
|---|---|---|
| **Unit** (210 tests, ~3 min) | In-process fakes simulate subprocess output, exits, timeouts | Every commit |
| **Integration** (24 tests, ~3 min) | Real PostgreSQL via Kubernetes; we issue `pg_terminate_backend` and reconnect | Every commit |
| **E2E local** (31 tests, ~5 min) | Real `fluid-postgres-mcp` subprocess; we send real `SIGTERM` and `SIGKILL` to script processes; we point the MCP at a deliberately broken URL | Every commit |
| **E2E live AWS** (9 tests, ~2 min) | Real AWS SSM tunnels, real EC2-hosted PostgreSQL, real `aws ssm send-command "docker compose stop postgres"` | On demand (needs `aws login`) |

Total: **274 tests passing**, exercising every fault listed above.

---

## How we capture the numbers in this doc

The detection/recovery/lost-query numbers under "Connection stability"
above come from a measurement harness
(`tests/e2e/test_outcomes_capture.py`) that runs each scenario 1–3
times against live PostgreSQL and records timings. We re-run it when
making changes that might affect timing, and update the numbers here.

The harness is skipped during normal test runs (set `CAPTURE_OUTCOMES=1`
to run it). It is not a regression test — it's a measurement tool.
