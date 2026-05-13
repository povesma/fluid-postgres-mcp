# Changelog

All notable changes to **fluid-postgres-mcp** are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.3] - 2026-05-14

### Added
- `--version` flag on the CLI. Prints
  `fluid-postgres-mcp X.Y.Z` and exits 0. Sourced live from
  `pyproject.toml` in source/editable installs and from installer
  metadata in wheel installs, so `--version` always matches the
  shipping artefact.
- "Verify install" section in README §Install documenting
  `uvx fluid-postgres-mcp --help` and `--version` as the
  post-install smoke check. Catches missing system libraries
  (`libpq`) and broken `uvx` cache before agent registration.
- `scripts/examples/aws-ssm-ec2-tunnel.py` — production-shaped
  reference pre-connect script for the PG-on-EC2 topology.
  Handles credential resolution, optional `sts:AssumeRole`,
  EC2 wake, SSM agent readiness, port-forward, port + PG
  liveness probes, handshake, signal teardown, remote session
  termination.
- `scripts/examples/aws-ssm-rds-tunnel.py` — same lifecycle as
  the EC2-direct example, but uses
  `AWS-StartPortForwardingSessionToRemoteHost` so an EC2 acts as a
  pure SSM forwarder to an RDS endpoint (no PG, no userspace
  proxy on the EC2).

### Changed
- README §AWS SSM example replaced by a structured §AWS SSM
  examples block: topology selector, env-var table, required AWS
  permissions, stdout protocol, per-topology subsections, and
  smoke guidance. Single source of truth for both scripts.
- sdist now ships `scripts/examples/*.py` for source consumers;
  the wheel remains unchanged (package-only).

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
