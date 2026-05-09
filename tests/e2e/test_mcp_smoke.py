"""Smoke test: boot MCP server, connect client, list tools, run a query."""

from __future__ import annotations

import pytest

from mcp_client_fixtures import call_tool
from mcp_client_fixtures import create_mcp_session
from mcp_client_fixtures import extract_text


@pytest.mark.asyncio
async def test_server_boots_and_lists_tools(k8s_pg_connection_string):
    connection_string, _ = k8s_pg_connection_string

    async for session in create_mcp_session(connection_string):
        tools = await session.list_tools()
        tool_names = [t.name for t in tools.tools]

        assert "execute_sql" in tool_names
        assert "status" in tool_names
        assert "list_schemas" in tool_names


@pytest.mark.asyncio
async def test_execute_sql_inline(k8s_pg_connection_string):
    connection_string, _ = k8s_pg_connection_string

    async for session in create_mcp_session(connection_string):
        result = await call_tool(session, "execute_sql", {"sql": "SELECT 42 AS answer"})
        text = extract_text(result)
        assert "42" in text
        assert not result.isError
