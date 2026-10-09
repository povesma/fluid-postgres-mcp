# Changelog

All notable changes to **fluid-postgres-mcp** are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.5] - 2026-10-10

### Added
- `--pre-connect-script` accepts quoted paths and arguments, with
  shell-style rules on macOS/Linux and Windows command-line rules on
  Windows (see README "Pre-connect scripts").
- On Windows, stopping the pre-connect script ends its whole process
  tree, also when the MCP itself crashes or is killed.

### Changed
- On macOS/Linux, `--pre-connect-script` values containing `'`, `"`,
  `\` or non-ASCII whitespace are now split differently. Check such
  values before upgrading.
- On macOS/Linux, the script gets `SIGTERM` and 5 s to clean up before
  `SIGKILL`.
- The script's stderr now goes to the MCP's stderr.
- A malformed or whitespace-only `--pre-connect-script` value makes
  the MCP exit with status 2 at startup.

### Fixed
- On Windows, the pre-connect script now starts; before, it never ran.
- The pre-connect script is now stopped on every MCP exit (client
  disconnect, `SIGTERM`/`SIGINT` including during startup, transport
  error), not only on a signal.
- On Python 3.12+, a script whose child process kept stdout open is
  now seen to exit, so shutdown no longer hangs.
- A failed first connect no longer leaves a connection pool
  reconnecting in the background.

## [0.1.4] - 2026-10-09

### Fixed
- Fresh installs no longer fail at startup with
  `No module named 'mcp.server.fastmcp'`; the `mcp` dependency is
  now pinned below 2.0. Upgrade if 0.1.3 fails to start.

## [0.1.3] - 2026-05-14

### Added
- `--version` flag prints the package version and exits 0.
- AWS SSM reference pre-connect scripts in `scripts/examples/`
  (PG on EC2; PG on RDS via an EC2 forwarder).

## [0.1.2] - 2026-05-11

### Changed
- `--pre-connect-script` may now be the sole DB URL source. The
  MCP no longer refuses to start when `DATABASE_URI` / positional
  URL is unset, as long as the script eventually emits
  `[MCP] DB_URL <url>`. `status` surfaces a new `WAITING_FOR_URL`
  state while the script hasn't produced a URL yet.

### Fixed
- README quick-install example
  (`uvx fluid-postgres-mcp --pre-connect-script /path/to/your-tunnel.sh`)
  now works without a separately configured `DATABASE_URI`.

[Unreleased]: https://github.com/povesma/fluid-postgres-mcp/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/povesma/fluid-postgres-mcp/releases/tag/v0.1.3
[0.1.2]: https://github.com/povesma/fluid-postgres-mcp/releases/tag/v0.1.2
