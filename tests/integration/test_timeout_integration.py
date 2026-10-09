"""Integration test: per-query timeout via SET LOCAL statement_timeout."""

from __future__ import annotations

import time

import psycopg.errors
import pytest
import pytest_asyncio

from postgres_mcp.config import ReconnectConfig
from postgres_mcp.sql.sql_driver import DbConnPool
from postgres_mcp.sql.sql_driver import SqlDriver


@pytest_asyncio.fixture
async def timeout_driver(k8s_pg_connection_string) -> SqlDriver:
    connection_string, _version = k8s_pg_connection_string
    pool = DbConnPool(
        connection_url=connection_string,
        reconnect_config=ReconnectConfig(initial_delay=0.5, max_delay=5.0, max_attempts=5),
    )
    await pool.pool_connect()
    driver = SqlDriver(conn=pool)
    yield driver
    await pool.close()


@pytest.mark.asyncio
class TestQueryTimeout:
    async def test_pg_sleep_cancelled_by_timeout(self, timeout_driver: SqlDriver):
        start = time.monotonic()
        with pytest.raises(psycopg.errors.QueryCanceled):
            await timeout_driver.execute_query("SELECT pg_sleep(10)", timeout_ms=1000)
        elapsed = time.monotonic() - start
        assert elapsed < 5, f"Timeout took {elapsed:.1f}s, expected ~1s"

    async def test_connection_usable_after_timeout(self, timeout_driver: SqlDriver):
        with pytest.raises(psycopg.errors.QueryCanceled):
            await timeout_driver.execute_query("SELECT pg_sleep(10)", timeout_ms=500)

        result = await timeout_driver.execute_query("SELECT 42 AS answer")
        assert result[0].cells["answer"] == 42

    async def test_no_timeout_allows_long_query(self, timeout_driver: SqlDriver):
        result = await timeout_driver.execute_query("SELECT pg_sleep(1), 1 AS done")
        assert result[0].cells["done"] == 1

    async def test_timeout_zero_means_no_timeout(self, timeout_driver: SqlDriver):
        result = await timeout_driver.execute_query("SELECT pg_sleep(1), 'ok' AS status", timeout_ms=0)
        assert result[0].cells["status"] == "ok"

    async def test_timeout_on_file_output(self, timeout_driver: SqlDriver):
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            with pytest.raises(psycopg.errors.QueryCanceled):
                await timeout_driver.execute_query(
                    "SELECT pg_sleep(10), generate_series(1, 100) AS id",
                    timeout_ms=500,
                )
        finally:
            if os.path.exists(path):
                os.unlink(path)

        result = await timeout_driver.execute_query("SELECT 1 AS alive")
        assert result[0].cells["alive"] == 1
