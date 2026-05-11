# Changelog

All notable changes to **fluid-postgres-mcp** are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] - 2026-05-11

### Added
- `ConnState.WAITING_FOR_URL` — pool is alive but has no URL yet
  (long-running `--pre-connect-script` has not emitted `[MCP] DB_URL`).
- `DbConnPool._unrecoverable` flag and `unrecoverable` read-only
  property — surfaces "no URL ever, give up" so callers can
  distinguish from the recoverable "no URL yet, hope for one" case.
- `--pre-connect-script` may now be the **sole** URL source: when
  set, the MCP starts without `DATABASE_URI` / positional URL and
  waits for the script to emit `[MCP] DB_URL <url>`.

### Changed
- `pool_connect()` return type → `Optional[AsyncConnectionPool]`.
  Long-running script + no URL → returns `None`, state
  `WAITING_FOR_URL`, no raise. Run-and-exit script exited without
  `DB_URL` and no URL configured → raises `ValueError`,
  `_unrecoverable=True`, state `ERROR`.
- `_reconnect_loop()` early-exits with `ConnectionError` when
  `_unrecoverable=True`; otherwise re-runs `ensure_ready()` each
  cycle and picks up a late `DB_URL` from the script.
- Startup URL-guard in `server.main()` now raises only when
  **neither** a URL **nor** `--pre-connect-script` is set. The
  error message names all three URL sources
  (`DATABASE_URI`, positional argument, `--pre-connect-script`
  + `[MCP] DB_URL`).
- README *Failure surface* extended with the two new no-URL
  outcomes (long-running recoverable; run-and-exit unrecoverable).

### Fixed
- README quick-install example (`uvx fluid-postgres-mcp
  --pre-connect-script /path/to/your-tunnel.sh`) now actually
  works without a separately configured `DATABASE_URI`.

[Unreleased]: https://github.com/povesma/fluid-postgres-mcp/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/povesma/fluid-postgres-mcp/releases/tag/v0.1.2
