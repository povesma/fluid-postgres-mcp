"""MCP stdio client fixtures for E2E tests.

Starts fluid-postgres-mcp as a subprocess, connects via MCP protocol,
and provides a ClientSession for tool calls.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any
from typing import AsyncGenerator

from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

SERVER_MODULE = "postgres_mcp"


def build_server_params(
    database_url: str,
    extra_args: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> StdioServerParameters:
    args = ["-m", SERVER_MODULE, database_url]
    if extra_args:
        args.extend(extra_args)

    env = {**os.environ}
    if env_overrides:
        env.update(env_overrides)

    return StdioServerParameters(
        command=sys.executable,
        args=args,
        env=env,
    )


async def create_mcp_session(
    database_url: str,
    extra_args: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> AsyncGenerator[ClientSession, None]:
    params = build_server_params(database_url, extra_args, env_overrides)
    logger.info("Starting MCP server: %s %s", params.command, " ".join(params.args))

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            logger.info("MCP session ready, tools: %s", tool_names)
            yield session


async def call_tool(session: ClientSession, name: str, args: dict[str, Any] | None = None) -> types.CallToolResult:
    return await session.call_tool(name, arguments=args or {})


def extract_text(result: types.CallToolResult) -> str:
    for content in result.content:
        if isinstance(content, types.TextContent):
            return content.text
    return ""
