# Tests

```bash
pytest tests/unit/         # in-process, fast
pytest tests/integration/  # needs Docker + a kubectl-accessible cluster
pytest tests/e2e/          # boots the MCP subprocess; SSM tests need `aws login`
```

When writing E2E tests that signal subprocesses, use `McpSession` from
`mcp_client_fixtures` (not `create_mcp_session`) so cancel scopes
enter/exit in the same task.
