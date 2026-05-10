# fluid-postgres-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

A PostgreSQL [MCP](https://modelcontextprotocol.io/) server for AI
agents. Streams large result sets to CSV, enforces per-query timeouts,
auto-reconnects with backoff, and supports long-running tunnel scripts
(e.g. AWS SSM port-forwarding) with credential rotation.

Fork of [crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp).

## Install

```bash
pip install -e .
```

Python 3.10+. Console entry point: `fluid-postgres-mcp`.

## Use it

Add to Claude Code (or any MCP client):

```bash
claude mcp add fluid-postgres-mcp -- \
    fluid-postgres-mcp postgresql://reader:pw@host:5432/db
```

Tools exposed: `execute_sql`, `status`, `list_schemas`, `list_objects`,
`get_object_details`, `explain_query`, `analyze_db_health`,
`analyze_query_indexes`, `analyze_workload_indexes`, `get_top_queries`.

`execute_sql` accepts `timeout_ms`, `output_file`, and `output_mode`
(`inline` / `file` / `file+inline`) for CSV streaming.

## Configure

Every flag has a matching env var (`PGMCP_*`). CLI wins.

| Flag | Default | What it does |
|---|---|---|
| `database_url` (positional) / `DATABASE_URI` | required | PostgreSQL URL |
| `--default-timeout` | `0` | `statement_timeout` ms (0 = none) |
| `--reconnect-initial-delay` / `--reconnect-max-delay` | `1.0` / `60.0` | Backoff bounds (s) |
| `--reconnect-max-attempts` | `0` | 0 = unlimited |
| `--pre-connect-script` | none | Tunnel/setup script (see below) |
| `--hook-timeout` | `30.0` | Pre-connect-script timeout (s) |
| `--event-buffer-size` | `100` | Per-category ring buffer |
| `--output-dir` | `.` | Default base for CSV output |
| `--transport` | `stdio` | `stdio` / `sse` / `streamable-http` |

## Pre-connect scripts

Two modes, auto-detected from script behaviour. Existing run-and-exit
scripts work unchanged.

**Run-and-exit:** the script runs, exits 0, and the MCP connects.
Suitable when something else owns the tunnel.

**Long-running:** the script owns the tunnel for the lifetime of the
MCP. It speaks a line-prefixed stdout protocol:

```
[MCP] DB_URL postgresql://user:pw@host:port/db    # optional, overrides --database-url
[MCP] READY_TO_CONNECT                            # required, signals readiness
```

If the script process dies (tunnel broke), the MCP detects it within
~1 second, respawns the script, and reconnects with whatever URL the
new instance emits — which is how credential/URL rotation works.

### AWS SSM example

```bash
#!/usr/bin/env bash
set -euo pipefail
LOCAL_PORT=15432

aws ssm start-session \
    --target "$EC2_INSTANCE_ID" \
    --document-name AWS-StartPortForwardingSession \
    --parameters "portNumber=5432,localPortNumber=$LOCAL_PORT" \
    >/dev/null 2>&1 &
TUNNEL_PID=$!

for _ in $(seq 1 30); do
    nc -z 127.0.0.1 "$LOCAL_PORT" 2>/dev/null && break
    sleep 1
done

PW=$(aws ssm get-parameter --name /db/password --with-decryption \
    --query Parameter.Value --output text)

printf '[MCP] DB_URL postgresql://reader:%s@127.0.0.1:%s/db\n' "$PW" "$LOCAL_PORT"
printf '[MCP] READY_TO_CONNECT\n'

wait "$TUNNEL_PID"   # script exits when the tunnel dies
```

Passwords are obfuscated in all event messages and logs.

### Reference scripts

Working examples used by the test suite — copy and adapt:

- [`tests/e2e/fixtures/long_running_passthrough.sh`](./tests/e2e/fixtures/long_running_passthrough.sh)
  — minimal long-running script: emits `DB_URL` + `READY_TO_CONNECT`,
  then blocks on `exec sleep` until SIGTERM. Useful as a starting
  template.
- [`tests/e2e/ssm_fixtures.py`](./tests/e2e/ssm_fixtures.py)
  (`create_long_running_tunnel_script`) — full SSM port-forwarding
  variant: spawns `aws ssm start-session` as a child, fetches the
  password from Parameter Store, emits the protocol lines, then
  `wait`s on the SSM child so tunnel death exits the script.
- [`tests/e2e/ssm_fixtures.py`](./tests/e2e/ssm_fixtures.py)
  (`create_tunnel_script`) — run-and-exit SSM variant for the legacy
  flow (something else owns the tunnel lifecycle).

### Authoring notes

- **Block on the resource that defines liveness.** A long-running
  script must exit when its tunnel/session dies; otherwise the MCP
  has no signal to reconnect. `wait "$TUNNEL_PID"` (foreground
  child) or `exec sleep <large>` (when there's no child to wait on)
  both work; backgrounded `sleep & wait $!` with a `trap` is
  unreliable on macOS (the parent's `proc.wait()` does not
  observe SIGCHLD through it).
- **macOS `sleep` does not accept `infinity`.** Use a large integer
  (`exec sleep 2147483647`).
- **Failure surface.** Exit-before-READY → mode is run-and-exit and
  exit code surfaces as success/failure. No `READY_TO_CONNECT`
  within `--hook-timeout` → script killed, connect fails. Malformed
  `[MCP] DB_URL` payload → warning event recorded, prior override
  retained, MCP falls back to the configured URL. Unknown `[MCP]`
  keywords are warned once per keyword and ignored.
- **Diagnose without shelling onto the box.** All script lifecycle
  events are exposed via the `status` MCP tool — `started`/`pid`,
  `READY_TO_CONNECT`, `DB_URL` (host/db only, password redacted),
  `exited`/`exit_code`. See [`TESTING-METHODOLOGY.md`](./TESTING-METHODOLOGY.md)
  for the full event catalogue.

## License

MIT — see [LICENSE](./LICENSE). Forked from
[crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp) (MIT).
