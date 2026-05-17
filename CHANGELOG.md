# Changelog

All notable changes to **fluid-postgres-mcp** are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
