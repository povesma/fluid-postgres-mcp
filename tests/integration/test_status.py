"""Integration test: status tool returns accurate state and history."""

from __future__ import annotations

import json

import psycopg.errors
import pytest
import pytest_asyncio

from postgres_mcp.config import ReconnectConfig
from postgres_mcp.event_store import EventCategory
from postgres_mcp.event_store import EventStore
from postgres_mcp.sql.sql_driver import ConnState
from postgres_mcp.sql.sql_driver import DbConnPool
from postgres_mcp.sql.sql_driver import SqlDriver


@pytest_asyncio.fixture
async def status_env(k8s_pg_connection_string):
    """Set up server globals with a real k8s connection and fresh EventStore."""
    import postgres_mcp.server as srv

    connection_string, _ = k8s_pg_connection_string
    original_db = srv.db_connection
    original_es = srv.event_store

    store = EventStore(buffer_size=50)

    def on_event(msg: str) -> None:
        store.record(EventCategory.EVENT, msg)

    pool = DbConnPool(
        connection_url=connection_string,
        reconnect_config=ReconnectConfig(initial_delay=0.5, max_delay=5.0, max_attempts=10),
        on_event=on_event,
    )
    await pool.pool_connect()

    srv.db_connection = pool
    srv.event_store = store

    yield pool, store

    srv.db_connection = original_db
    srv.event_store = original_es
    await pool.close()


@pytest.mark.asyncio
class TestStatusToolIntegration:
    async def test_connected_state(self, status_env):
        pool, store = status_env
        from postgres_mcp.server import status

        result = await status()
        text = result[0].text
        parsed = eval(text)
        assert parsed["state"] == "connected"

    async def test_state_after_query(self, status_env):
        pool, store = status_env
        from postgres_mcp.server import status

        driver = SqlDriver(conn=pool)
        await driver.execute_query("SELECT 1")

        result = await status()
        parsed = eval(result[0].text)
        assert parsed["state"] == "connected"

    async def test_events_recorded_on_connect(self, status_env):
        pool, store = status_env
        from postgres_mcp.server import status

        result = await status(events=10)
        parsed = eval(result[0].text)
        event_msgs = [e["message"] for e in parsed.get("events", [])]
        assert any("Connected" in m for m in event_msgs)

    async def test_metadata_with_reconnect_count(self, status_env, k8s_pg_connection_string):
        pool, store = status_env
        connection_string, _ = k8s_pg_connection_string
        from postgres_mcp.server import status

        import asyncio

        import psycopg

        pids = set()
        for _ in range(5):
            try:
                async with pool.pool.connection() as conn:
                    cur = await conn.execute("SELECT pg_backend_pid()")
                    row = await cur.fetchone()
                    if row:
                        pids.add(row[0])
            except Exception:
                break

        killer = await psycopg.AsyncConnection.connect(connection_string, autocommit=True)
        for pid in pids:
            await killer.execute(f"SELECT pg_terminate_backend({pid})")
        await killer.close()
        await asyncio.sleep(0.5)

        pool.mark_invalid("test: forced disconnect")
        driver = SqlDriver(conn=pool)
        await driver.execute_query("SELECT 1")

        result = await status(metadata=True, events=10)
        parsed = eval(result[0].text)
        assert parsed["metadata"]["reconnect_count"] >= 1

        event_msgs = [e["message"] for e in parsed.get("events", [])]
        assert any("Reconnect" in m for m in event_msgs)

    async def test_error_events_after_timeout(self, status_env):
        pool, store = status_env
        from postgres_mcp.server import status

        driver = SqlDriver(conn=pool)
        try:
            await driver.execute_query("SELECT pg_sleep(10)", timeout_ms=500)
        except psycopg.errors.QueryCanceled:
            store.record(EventCategory.ERROR, "Query timed out")

        result = await status(errors=5)
        parsed = eval(result[0].text)
        error_msgs = [e["message"] for e in parsed.get("errors", [])]
        assert any("timed out" in m for m in error_msgs)

    async def test_no_credentials_in_output(self, status_env):
        pool, store = status_env
        from postgres_mcp.server import status

        pool._last_error = f"failed at {pool.connection_url}"
        store.record(EventCategory.ERROR, f"connection error: {pool.connection_url}")

        result = await status(errors=5, metadata=True)
        text = result[0].text
        assert "testpass" not in text

    async def test_full_sequence(self, status_env, k8s_pg_connection_string):
        """Run queries, force a drop, reconnect, then verify status reflects the full history."""
        pool, store = status_env
        connection_string, _ = k8s_pg_connection_string
        from postgres_mcp.server import status

        import asyncio

        import psycopg

        driver = SqlDriver(conn=pool)
        await driver.execute_query("SELECT 1")
        await driver.execute_query("SELECT 2")

        pids = set()
        for _ in range(5):
            try:
                async with pool.pool.connection() as conn:
                    cur = await conn.execute("SELECT pg_backend_pid()")
                    row = await cur.fetchone()
                    if row:
                        pids.add(row[0])
            except Exception:
                break

        killer = await psycopg.AsyncConnection.connect(connection_string, autocommit=True)
        for pid in pids:
            await killer.execute(f"SELECT pg_terminate_backend({pid})")
        await killer.close()
        await asyncio.sleep(0.5)

        pool.mark_invalid("test: full sequence disconnect")
        await driver.execute_query("SELECT 3")

        result = await status(events=20, metadata=True)
        parsed = eval(result[0].text)

        assert parsed["state"] == "connected"
        assert parsed["metadata"]["reconnect_count"] >= 1

        event_msgs = [e["message"] for e in parsed.get("events", [])]
        assert any("Connected" in m for m in event_msgs)
        assert any("Reconnect" in m for m in event_msgs)
        assert any("Connection lost" in m for m in event_msgs)
