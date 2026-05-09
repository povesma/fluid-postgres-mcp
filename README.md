# fluid-postgres-mcp

Fluid PostgreSQL MCP Server — reliable database interface for AI agents.

Fork of [crystaldba/postgres-mcp](https://github.com/crystaldba/postgres-mcp) with:
- File output streaming (COPY TO STDOUT)
- Per-query timeouts (SET LOCAL statement_timeout)
- Automatic reconnection with exponential backoff
- Pre-connection hooks
- Connection status tool with event history
- Progress notifications
