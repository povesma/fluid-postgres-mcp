"""E2E test: server lifecycle — bad connection, graceful shutdown."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time

import pytest

from mcp_client_fixtures import call_tool
from mcp_client_fixtures import create_mcp_session
from mcp_client_fixtures import extract_text


BAD_URL = "postgresql://user:pass@192.0.2.1:5432/nonexistent?connect_timeout=2"
BAD_CONN_ARGS = [
    "--reconnect-max-attempts", "2",
    "--reconnect-initial-delay", "0.5",
    "--reconnect-max-delay", "1",
]


@pytest.mark.asyncio
class TestBadConnectionString:
    async def test_server_stays_alive_with_unreachable_host(self):
        async for session in create_mcp_session(BAD_URL, extra_args=BAD_CONN_ARGS):
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            assert "execute_sql" in tool_names
            assert "status" in tool_names

    async def test_execute_sql_returns_error_with_bad_connection(self):
        async for session in create_mcp_session(BAD_URL, extra_args=BAD_CONN_ARGS):
            result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
            assert result.isError
            text = extract_text(result)
            assert len(text) > 0

    async def test_status_shows_state_with_bad_connection(self):
        async for session in create_mcp_session(BAD_URL, extra_args=BAD_CONN_ARGS):
            result = await call_tool(session, "status", {"events": 10})
            assert not result.isError
            text = extract_text(result)
            parsed = eval(text)
            assert parsed["state"] in ("disconnected", "error", "reconnecting")


@pytest.mark.asyncio
class TestGracefulShutdown:
    async def test_sigterm_exits_cleanly(self):
        dummy_url = "postgresql://user:pass@192.0.2.1:5432/db?connect_timeout=1"

        proc = subprocess.Popen(
            [sys.executable, "-m", "postgres_mcp", dummy_url],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": "src"},
        )

        time.sleep(3)
        assert proc.poll() is None, "Server died before we could send SIGTERM"

        proc.send_signal(signal.SIGTERM)

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("Server did not exit within 10s after SIGTERM")

    async def test_server_responds_then_shuts_down(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            result = await call_tool(session, "execute_sql", {"sql": "SELECT 'alive' AS s"})
            assert not result.isError
            assert "alive" in extract_text(result)
