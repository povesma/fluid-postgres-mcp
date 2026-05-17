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

### Verify install

Before wiring the MCP into your agent, confirm the install actually
resolved its dependencies:

```bash
uvx fluid-postgres-mcp --version    # prints "fluid-postgres-mcp X.Y.Z", exit 0
uvx fluid-postgres-mcp --help       # prints usage, exit 0
```

Exit 0 from either command means the package downloaded and every
runtime dependency imported successfully. A Python traceback or
non-zero exit means at least one import failed — typically a missing
system library (e.g. `libpq` on minimal Linux images) or a broken
`uvx` cache. Fix that before continuing; an agent registration
against a broken install fails silently at first tool call.

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

### AWS SSM examples

Two vendored Python reference scripts cover the common AWS topologies.
Each is a single drop-in file: copy it, set the env vars, point
`--pre-connect-script` at it. Both speak the long-running protocol
described above; both supervise their SSM child and exit on its
death so fluid-postgres-mcp respawns them.

#### Choosing a topology

| Topology | Script | When |
|---|---|---|
| **EC2-direct** | [`scripts/examples/aws-ssm-ec2-tunnel.py`](./scripts/examples/aws-ssm-ec2-tunnel.py) | PostgreSQL runs on the EC2 instance itself, or the EC2 hosts a userspace proxy you control. |
| **RDS-via-EC2** | [`scripts/examples/aws-ssm-rds-tunnel.py`](./scripts/examples/aws-ssm-rds-tunnel.py) | PostgreSQL runs on RDS. The EC2 is a pure SSM forwarder — no PG, no proxy on it. Uses `AWS-StartPortForwardingSessionToRemoteHost`. |

True bastion-less SSM-to-RDS is not possible: `ssm:StartSession`
requires an SSM-managed target, and RDS is not one. If you cannot
keep an EC2 in the loop, look at RDS IAM authentication or the EC2
Instance Connect Endpoint (EICE) — both are outside the SSM
port-forwarding model these scripts use.

Passwords in `DB_URL` are obfuscated in every fluid-postgres-mcp
event message and log line.

#### Environment variables

The scripts are configured entirely via environment variables,
passed through your agent's MCP registration (e.g. `claude mcp add
… -e KEY=VALUE`).

| Variable | EC2-direct | RDS-via-EC2 | Purpose |
|---|---|---|---|
| `EC2_INSTANCE_ID` | required | required | SSM target instance |
| `EC2_REGION` | required | required | AWS region of the instance |
| `RDS_ENDPOINT` | — | required | RDS endpoint hostname |
| `DB_NAME` | required | required | PostgreSQL database name |
| `DB_USERNAME` | required | required | PostgreSQL user |
| `DB_PASSWD` | required | required | PostgreSQL password |
| `DB_HOST` | optional (`localhost`) | — | Host PG listens on (EC2-direct only) |
| `DB_PORT` | optional (`5432`) | optional (`5432`) | PostgreSQL port |
| `ASSUME_ROLE_ARN` | optional | optional | Role to assume on top of base credentials |
| `AWS_PROFILE` | optional | optional | Profile (overridden by `--profile` flag) |

Authentication precedence (highest first): `--profile` CLI flag →
`AWS_PROFILE` → SDK default credential chain (env vars,
`~/.aws/credentials`, instance metadata, …). If `ASSUME_ROLE_ARN`
is set, the resolved base credentials drive an `sts:AssumeRole`
call and the resulting STS credentials drive every subsequent AWS
call.

#### Required AWS permissions

The principal that ends up driving the AWS calls (after AssumeRole,
if any) needs:

```
sts:AssumeRole                       (only if ASSUME_ROLE_ARN set)
sts:GetCallerIdentity                (diagnostic)
ec2:DescribeInstances
ec2:StartInstances                   (only to wake a stopped host)
ssm:DescribeInstanceInformation
ssm:StartSession                     (see below for resource scope)
ssm:TerminateSession                 (on the session ARN — clean teardown)
```

The `ssm:StartSession` resource scope differs by topology:

- **EC2-direct**: target = the EC2 instance ARN; document =
  `AWS-StartPortForwardingSession`.
- **RDS-via-EC2**: target = the EC2 instance ARN; document =
  `AWS-StartPortForwardingSessionToRemoteHost`. The EC2's security
  group must allow egress to RDS:5432; the RDS security group must
  allow ingress from the EC2 security group.

#### Stdout protocol

Both scripts emit exactly two lines on stdout (everything else goes
to stderr):

```
[MCP] DB_URL postgresql://<user>:<pw>@127.0.0.1:<local_port>/<db>?...
[MCP] READY_TO_CONNECT
```

After these the script stays alive supervising the SSM child. Exit
on child death is the signal to fluid-postgres-mcp that the tunnel
is gone and the script should be respawned — that is the recovery
loop.

#### EC2-direct

PostgreSQL is reachable on the EC2 itself (running there, or
proxied by the EC2 to a backend it controls). The SSM session
terminates on the EC2 and forwards traffic to whatever
`DB_HOST:DB_PORT` resolves to from the EC2's perspective
(default `localhost:5432`).

```bash
claude mcp add my-pg \
  -e EC2_INSTANCE_ID=i-0123456789abcdef0 \
  -e EC2_REGION=eu-central-1 \
  -e DB_NAME=mydb -e DB_USERNAME=reader -e DB_PASSWD='s3cr3t' \
  -- uvx fluid-postgres-mcp \
       --pre-connect-script /path/to/aws-ssm-ec2-tunnel.py
```

#### RDS-via-EC2

PostgreSQL runs on RDS. The EC2 is a pure SSM forwarder — no PG
process, no userspace proxy. The SSM session uses
`AWS-StartPortForwardingSessionToRemoteHost` with `host=$RDS_ENDPOINT`,
so traffic flows
`localhost:<local_port> → EC2 (SSM forwarder) → $RDS_ENDPOINT:5432`.

```bash
claude mcp add my-pg \
  -e EC2_INSTANCE_ID=i-0123456789abcdef0 \
  -e EC2_REGION=eu-central-1 \
  -e RDS_ENDPOINT=my-db.abcdef.eu-central-1.rds.amazonaws.com \
  -e DB_NAME=mydb -e DB_USERNAME=reader -e DB_PASSWD='s3cr3t' \
  -- uvx fluid-postgres-mcp \
       --pre-connect-script /path/to/aws-ssm-rds-tunnel.py
```

#### Smoke

After registering, restart your agent and run a real-data query —
not `SELECT 1`. A constant query proves only that something
answered on the socket; it doesn't prove the right DB was mapped
or that rows flow:

```sql
SELECT count(*) FROM <a-known-populated-table>;
```

Expect a non-trivial count you can recognise. Zero / empty / NULL
is a failure, not a pass.

### Reference scripts

Working examples — copy and adapt:

- [`scripts/examples/aws-ssm-ec2-tunnel.py`](./scripts/examples/aws-ssm-ec2-tunnel.py)
  — production-shaped Python: credential resolution, optional
  `sts:AssumeRole`, EC2 wake, SSM agent readiness wait, port-forward,
  port-open probe, PG liveness probe, handshake, signal teardown,
  remote session termination. EC2-direct topology.
- [`scripts/examples/aws-ssm-rds-tunnel.py`](./scripts/examples/aws-ssm-rds-tunnel.py)
  — same lifecycle as above, but uses
  `AWS-StartPortForwardingSessionToRemoteHost` so the EC2 acts as a
  pure SSM forwarder to an RDS endpoint.
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
  Long-running script alive but no `[MCP] DB_URL` yet → state is
  `WAITING_FOR_URL`, the reconnect loop keeps polling, recoverable
  as soon as the script emits a URL. Run-and-exit script exited
  *without* emitting `DB_URL` and no `DATABASE_URI` / positional URL
  is set → `_unrecoverable=True`, state `ERROR`, no further retries.
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

Versioning is SemVer. **PyPI is the point of no return** — a
version uploaded there cannot be edited or reuploaded, only
yanked. Everything reversible happens before PyPI publish; GitHub
Release stays in draft until PyPI succeeds. A release is not done
until **all seven steps** are completed.

```bash
# 1. CHANGELOG + version bump (release commit):
git add CHANGELOG.md pyproject.toml
git commit -m "chore(release): bump version to X.Y.Z"
git tag -a vX.Y.Z -m "Release X.Y.Z - <one-line summary>"

# 2. Clean and build (build deps via uvx):
rm -rf dist/ build/ *.egg-info
uvx --from build pyproject-build

# 3. Inspect sdist + twine check. The wheel only ships
#    src/postgres_mcp; the sdist is allowlisted in pyproject.toml
#    [tool.hatch.build.targets.sdist], so .env, .claude, tasks/
#    must NOT appear:
tar -tzf dist/*.tar.gz | sort
.venv/bin/twine check dist/*

# 4. Push commit and tag:
git push
git push origin vX.Y.Z

# 5. Draft GitHub Release. Body is HAND-WRITTEN per the Release
#    body rule above — do not awk-extract from CHANGELOG (the
#    audiences and styles differ). Draft state lets you proof-read
#    against the rendered Release page before PyPI is committed.
$EDITOR /tmp/release-vX.Y.Z.md   # write the executive summary
gh release create vX.Y.Z --draft -t "vX.Y.Z" -F /tmp/release-vX.Y.Z.md
# Open the draft URL printed above, review the rendered body.
# Fix with `gh release edit vX.Y.Z --notes-file …` — still cheap;
# PyPI is not yet involved.

# 6. Upload to PyPI — *point of no return*. Twine's auth contract
#    is TWINE_USERNAME / TWINE_PASSWORD, not PYPI_TOKEN, so source
#    .env to get PYPI_TOKEN into the environment and pass via -u/-p.
#    `set -a; source .env; set +a` keeps the value in this shell;
#    the token never enters argv or command history. If output
#    might be teed (assistant logs, CI), pipe through a redactor:
#      … | sed 's/pypi-[A-Za-z0-9_-]*/pypi-<REDACTED>/g'
set -a; source .env; set +a
.venv/bin/twine upload -u __token__ -p "$PYPI_TOKEN" dist/*

# 7. Flip the GH Release out of draft and smoke the published
#    artefact end-to-end. Both must pass before the release is
#    considered done:
gh release edit vX.Y.Z --draft=false
uvx fluid-postgres-mcp --version    # expect "fluid-postgres-mcp X.Y.Z"
uvx fluid-postgres-mcp --help       # expect non-empty usage, exit 0
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
- `CHANGELOG.md` follows
  [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/);
  the entry is mandatory before the version bump (treat it as a
  release gate alongside the `tar -tzf` listing).
- **CHANGELOG.md authoring.** *Audience: someone deciding whether
  to upgrade, and someone reconstructing history later (downstream
  packagers, future-you, anyone tracing a regression to a version).*
  Comprehensive but ruthless with wording. Include all user-facing
  changes, categorised by impact (Breaking, Security, Added,
  Changed, Fixed, Deprecated). Each item: one sentence stating the
  change and its user impact. Add a second sentence only when a
  reader must take action (migration step, version range affected,
  workaround) — never to explain rationale or implementation. Drop
  the *how* and the *why*; if rationale matters, it lives in the
  commit message or PR.
- **GitHub Release body authoring.** *Audience: someone glancing
  at the release page or a notification feed, deciding whether
  this release needs their attention right now.* Executive summary.
  Open with the most consequential change in one sentence; if the
  release has a coherent theme, name it — if not, don't invent one.
  Follow with a "Highlights" bullet list of anything a reader of
  the release page needs to know without opening the CHANGELOG.
  Breaking, security, deprecations, platform/Python/dependency
  shifts, and major features will usually qualify; pure bug fixes
  and internal changes will not. Link to the CHANGELOG for the
  rest. Hand-written, not awk-extracted.

## License

MIT — see [LICENSE](./LICENSE). Forked from
[crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp) (MIT).
