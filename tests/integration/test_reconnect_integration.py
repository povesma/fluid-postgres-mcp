"""Integration test: pg_terminate_backend() drops connection, reconnect succeeds."""

from __future__ import annotations

import asyncio

import psycopg
import pytest
import pytest_asyncio

from postgres_mcp.config import ReconnectConfig
from postgres_mcp.sql.sql_driver import ConnState
from postgres_mcp.sql.sql_driver import DbConnPool
from postgres_mcp.sql.sql_driver import SqlDriver


@pytest_asyncio.fixture
async def make_driver(k8s_pg_connection_string):
    """Factory that creates a fresh driver per test to avoid shared-state issues."""
    drivers = []

    async def _make(initial_delay: float = 0.5, max_delay: float = 5.0, max_attempts: int = 10) -> SqlDriver:
        connection_string, _version = k8s_pg_connection_string
        pool = DbConnPool(
            connection_url=connection_string,
            reconnect_config=ReconnectConfig(
                initial_delay=initial_delay,
                max_delay=max_delay,
                max_attempts=max_attempts,
            ),
        )
        await pool.pool_connect()
        driver = SqlDriver(conn=pool)
        drivers.append(pool)
        return driver

    yield _make

    for p in drivers:
        await p.close()


async def _get_backend_pid(driver: SqlDriver) -> int:
    rows = await driver.execute_query("SELECT pg_backend_pid() AS pid")
    return rows[0].cells["pid"]


async def _terminate_pid(connection_string: str, pid: int) -> bool:
    conn = await psycopg.AsyncConnection.connect(connection_string, autocommit=True)
    try:
        cur = await conn.execute(f"SELECT pg_terminate_backend({pid})")
        row = await cur.fetchone()
        return row[0] if row else False
    finally:
        await conn.close()


async def _force_pool_error(driver: SqlDriver, connection_string: str) -> None:
    """Terminate all pool connections by getting each one's PID and killing it."""
    pool: DbConnPool = driver.conn
    async_pool = pool.pool

    pids = set()
    for _ in range(5):
        try:
            async with async_pool.connection() as conn:
                cur = await conn.execute("SELECT pg_backend_pid()")
                row = await cur.fetchone()
                if row:
                    pids.add(row[0])
        except Exception:
            break

    for pid in pids:
        await _terminate_pid(connection_string, pid)

    await asyncio.sleep(0.5)


@pytest.mark.asyncio
class TestReconnectAfterTerminate:
    async def test_query_after_terminate_triggers_reconnect(self, make_driver, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string
        driver = await make_driver()
        pool: DbConnPool = driver.conn

        result = await driver.execute_query("SELECT 1 AS alive")
        assert result[0].cells["alive"] == 1

        await _force_pool_error(driver, connection_string)
        pool.mark_invalid("test: connections terminated")

        result = await driver.execute_query("SELECT 2 AS recovered")
        assert result[0].cells["recovered"] == 2
        assert pool.state == ConnState.CONNECTED
        assert pool.reconnect_count >= 1

    async def test_reconnect_count_increments(self, make_driver, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string
        driver = await make_driver()
        pool: DbConnPool = driver.conn

        for i in range(3):
            await _force_pool_error(driver, connection_string)
            pool.mark_invalid(f"test: round {i}")
            result = await driver.execute_query(f"SELECT {i} AS val")
            assert result[0].cells["val"] == i

        assert pool.reconnect_count >= 3

    async def test_data_integrity_after_reconnect(self, make_driver, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string
        driver = await make_driver()

        await driver.execute_query(
            "CREATE TABLE IF NOT EXISTS test_reconnect_data (id serial PRIMARY KEY, data text)"
        )
        await driver.execute_query(
            "INSERT INTO test_reconnect_data (data) VALUES ('before_kill')"
        )

        await _force_pool_error(driver, connection_string)
        driver.conn.mark_invalid("test: kill for integrity check")

        await driver.execute_query(
            "INSERT INTO test_reconnect_data (data) VALUES ('after_kill')"
        )
        rows = await driver.execute_query(
            "SELECT data FROM test_reconnect_data ORDER BY id"
        )
        values = [r.cells["data"] for r in rows]
        assert "before_kill" in values
        assert "after_kill" in values

        await driver.execute_query("DROP TABLE IF EXISTS test_reconnect_data")

    async def test_pool_survives_single_backend_termination(self, make_driver, k8s_pg_connection_string):
        """Pool transparently replaces a terminated connection — queries keep working."""
        connection_string, _ = k8s_pg_connection_string
        driver = await make_driver()

        pid = await _get_backend_pid(driver)
        await _terminate_pid(connection_string, pid)
        await asyncio.sleep(0.5)

        result = await driver.execute_query("SELECT 'ok' AS status")
        assert result[0].cells["status"] == "ok"
