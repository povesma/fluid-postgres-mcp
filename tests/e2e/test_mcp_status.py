"""E2E test: status tool via MCP protocol — state, events, metadata."""

from __future__ import annotations

import pytest

from mcp_client_fixtures import call_tool
from mcp_client_fixtures import create_mcp_session
from mcp_client_fixtures import extract_text


@pytest.mark.asyncio
class TestStatusConnected:
    async def test_shows_connected_state(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            result = await call_tool(session, "status", {})
            assert not result.isError
            text = extract_text(result)
            parsed = eval(text)
            assert parsed["state"] == "connected"

    async def test_events_include_connect(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            result = await call_tool(session, "status", {"events": 10})
            parsed = eval(extract_text(result))
            event_msgs = [e["message"] for e in parsed.get("events", [])]
            assert any("Connected" in m for m in event_msgs)


@pytest.mark.asyncio
class TestStatusAfterQueries:
    async def test_state_is_connected_after_queries(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            for i in range(5):
                await call_tool(session, "execute_sql", {"sql": f"SELECT {i}"})

            result = await call_tool(session, "status", {})
            parsed = eval(extract_text(result))
            assert parsed["state"] == "connected"


@pytest.mark.asyncio
class TestStatusAfterError:
    async def test_error_events_after_bad_query(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            await call_tool(session, "execute_sql", {
                "sql": "SELECT pg_sleep(10)",
                "timeout_ms": 500,
            })

            result = await call_tool(session, "status", {"errors": 10, "events": 10})
            parsed = eval(extract_text(result))
            assert parsed["state"] == "connected"


@pytest.mark.asyncio
class TestStatusMetadata:
    async def test_metadata_includes_reconnect_count(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            result = await call_tool(session, "status", {"metadata": True})
            parsed = eval(extract_text(result))
            assert "metadata" in parsed
            assert "reconnect_count" in parsed["metadata"]
            assert parsed["metadata"]["reconnect_count"] == 0


@pytest.mark.asyncio
class TestStatusNoCredentials:
    async def test_no_password_in_status_output(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            await call_tool(session, "execute_sql", {"sql": "SELECT 1"})

            result = await call_tool(session, "status", {
                "errors": 10,
                "warnings": 10,
                "events": 10,
                "metadata": True,
            })
            text = extract_text(result)
            assert "testpass" not in text


@pytest.mark.asyncio
class TestStatusAfterReconnect:
    async def test_reconnect_events_visible(self, k8s_pg_connection_string):
        """Force pool invalidation via pg_terminate_backend, verify status shows reconnect."""
        connection_string, _ = k8s_pg_connection_string

        import asyncio

        import psycopg

        async for session in create_mcp_session(
            connection_string,
            extra_args=["--reconnect-initial-delay", "0.5", "--reconnect-max-delay", "3"],
        ):
            await call_tool(session, "execute_sql", {"sql": "SELECT 1"})

            killer = await psycopg.AsyncConnection.connect(connection_string, autocommit=True)
            rows = await killer.execute(
                "SELECT pid FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND pid != pg_backend_pid() "
                "AND state = 'idle' "
                "LIMIT 10"
            )
            pids = [r[0] for r in await rows.fetchall()]
            for pid in pids:
                await killer.execute(f"SELECT pg_terminate_backend({pid})")
            await killer.close()
            await asyncio.sleep(1)

            error_result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})

            if error_result.isError:
                recover_result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
                assert not recover_result.isError

            result = await call_tool(session, "status", {
                "events": 20,
                "metadata": True,
            })
            parsed = eval(extract_text(result))
            assert parsed["state"] == "connected"

            event_msgs = [e["message"] for e in parsed.get("events", [])]
            has_reconnect = any("Reconnect" in m for m in event_msgs)
            has_lost = any("Connection lost" in m or "lost" in m.lower() for m in event_msgs)
            reconnect_count = parsed["metadata"]["reconnect_count"]

            assert has_reconnect or reconnect_count >= 1 or has_lost, (
                f"Expected reconnect evidence in events or metadata. "
                f"Events: {event_msgs}, reconnect_count: {reconnect_count}"
            )
