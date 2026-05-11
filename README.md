# fluid-postgres-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

A PostgreSQL [MCP](https://modelcontextprotocol.io/) server for AI
agents. Streams large result sets to CSV, enforces per-query timeouts,
auto-reconnects with backoff, and supports long-running tunnel scripts
(e.g. AWS SSM port-forwarding) with credential rotation.

Fork of [crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp).

## Install

> Jump to: [Other AI agents](#other-ai-agents) ·
> [Alternative install methods](#alternative-install-methods) ·
> [Develop](#develop)

Python 3.10+. Published on PyPI as
[`fluid-postgres-mcp`](https://pypi.org/project/fluid-postgres-mcp/);
console entry point of the same name.

### With Claude Code (primary)

```bash
claude mcp add fluid-postgres-mcp -- \
    uvx fluid-postgres-mcp \
        postgresql://reader:pw@host:5432/db
```

With a long-running tunnel script (see
[Pre-connect scripts](#pre-connect-scripts) for the protocol the
script must speak):

```bash
claude mcp add fluid-postgres-mcp -- \
    uvx fluid-postgres-mcp \
        --pre-connect-script /path/to/your-tunnel.sh
```

### Other AI agents

Brief one-shot snippets — copy-paste, or read your agent's own MCP
docs for the full story. All entries use `uvx fluid-postgres-mcp`
so no global install is needed.

**Codex CLI** —
[docs](https://github.com/openai/codex/blob/main/docs/config.md):

```bash
codex mcp add fluid-postgres-mcp \
    --transport stdio \
    --command "uvx fluid-postgres-mcp postgresql://reader:pw@host:5432/db"
```

**Cursor CLI** — [docs](https://cursor.com/docs/cli):

```bash
agent mcp add fluid-postgres-mcp -- \
    uvx fluid-postgres-mcp postgresql://reader:pw@host:5432/db
```

**Gemini CLI** — add to `~/.gemini/settings.json`
([docs](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md)):

```json
{
  "mcpServers": {
    "fluid-postgres-mcp": {
      "command": "uvx",
      "args": ["fluid-postgres-mcp", "postgresql://reader:pw@host:5432/db"]
    }
  }
}
```

**opencode** — add to `opencode.json`
([docs](https://opencode.ai/docs/mcp-servers)):

```jsonc
{
  "mcp": {
    "fluid-postgres-mcp": {
      "type": "local",
      "command": ["uvx", "fluid-postgres-mcp", "postgresql://reader:pw@host:5432/db"]
    }
  }
}
```

**Kiro CLI** — add to `mcp.json`
([docs](https://kiro.dev/docs/cli/mcp)):

```json
{
  "mcpServers": {
    "fluid-postgres-mcp": {
      "command": "uvx",
      "args": ["fluid-postgres-mcp", "postgresql://reader:pw@host:5432/db"]
    }
  }
}
```

**Cursor (IDE)** — add to `~/.cursor/mcp.json`
([docs](https://cursor.com/docs/mcp)):

```json
{
  "mcpServers": {
    "fluid-postgres-mcp": {
      "command": "uvx",
      "args": ["fluid-postgres-mcp", "postgresql://reader:pw@host:5432/db"]
    }
  }
}
```

**Windsurf** — add to `~/.codeium/windsurf/mcp_config.json`
([docs](https://docs.windsurf.com/plugins/cascade/mcp)):

```json
{
  "mcpServers": {
    "fluid-postgres-mcp": {
      "command": "uvx",
      "args": ["fluid-postgres-mcp", "postgresql://reader:pw@host:5432/db"]
    }
  }
}
```

**Zed** — add to `~/.config/zed/settings.json` under
`context_servers` (note: *not* `mcpServers`)
([docs](https://zed.dev/docs/ai/mcp)):

```json
{
  "context_servers": {
    "fluid-postgres-mcp": {
      "command": "uvx",
      "args": ["fluid-postgres-mcp", "postgresql://reader:pw@host:5432/db"]
    }
  }
}
```

### Alternative install methods

If you'd rather have a persistent install than resolve through
`uvx` on every launch:

```bash
pipx install fluid-postgres-mcp        # isolated, on $PATH
pip  install fluid-postgres-mcp        # use a virtualenv to avoid global pollution
```

From source (no editable; for users who clone but don't want a
working tree):

```bash
git clone https://github.com/povesma/fluid-postgres-mcp
pip install ./fluid-postgres-mcp
```

After any of these, the agent snippets above can drop `uvx` and
invoke `fluid-postgres-mcp` directly.

## How to use Fluid Postgres MCP

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
  `exited`/`exit_code`. See [`ARCHITECTURE.md`](./ARCHITECTURE.md)
  for how the event store is wired and
  [`TESTING-METHODOLOGY.md`](./TESTING-METHODOLOGY.md) for the faults
  we inject against it.

## Develop

Work from a clone:

```bash
git clone https://github.com/povesma/fluid-postgres-mcp
cd fluid-postgres-mcp
pip install -e ".[dev]"
pytest
```

Design and fault-injection catalogue:
[`ARCHITECTURE.md`](./ARCHITECTURE.md) ·
[`TESTING-METHODOLOGY.md`](./TESTING-METHODOLOGY.md).

### Release

Versioning is SemVer; PyPI is the source of truth. The flow that
produced v0.1.1:

```bash
# 1. Bump version in pyproject.toml, then:
git add pyproject.toml
git commit -m "chore(release): bump version to X.Y.Z"
git tag -a vX.Y.Z -m "Release X.Y.Z - <one-line summary>"

# 2. Clean and build (build deps via uvx, no global install needed):
rm -rf dist/ build/ *.egg-info
uvx --from build pyproject-build

# 3. Inspect what's actually inside the sdist before publishing.
#    The wheel only ships src/postgres_mcp; the sdist is allowlisted
#    in pyproject.toml [tool.hatch.build.targets.sdist], so anything
#    not in that list must NOT appear here — especially .env, .claude,
#    tasks/, or any other working-tree-only file:
tar -tzf dist/*.tar.gz | sort
.venv/bin/twine check dist/*

# 4. Push commit and tag:
git push
git push origin vX.Y.Z

# 5. Upload to PyPI. Twine's auth contract is TWINE_USERNAME /
#    TWINE_PASSWORD — not PYPI_TOKEN — so source .env to get
#    PYPI_TOKEN into the environment, then pass it via -u/-p so
#    the bridge is explicit. `set -a; source .env; set +a` keeps
#    the value confined to this shell; the token never enters
#    command line history or any tool's stdin/stdout:
set -a; source .env; set +a
.venv/bin/twine upload -u __token__ -p "$PYPI_TOKEN" dist/*
```

Notes:
- `uvx --from build pyproject-build` avoids needing `python -m build`
  installed system-wide; the project's hatchling backend is fetched
  into an isolated env.
- The sdist contents are controlled by an explicit allowlist in
  `[tool.hatch.build.targets.sdist].include`. Any new top-level
  file you add to the repo is excluded from the sdist by default —
  add it to the allowlist if it should ship. Treat the step-3
  `tar -tzf` listing as a release gate, not a curiosity.
- After tagging, optionally create a GitHub Release from the tag:
  `gh release create vX.Y.Z -t "vX.Y.Z" -n "<notes>"`.

## License

MIT — see [LICENSE](./LICENSE). Forked from
[crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp) (MIT).
