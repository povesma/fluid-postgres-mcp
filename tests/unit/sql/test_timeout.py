from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import call
from unittest.mock import patch

import pytest

from postgres_mcp.sql.sql_driver import DbConnPool
from postgres_mcp.sql.sql_driver import SqlDriver


def _make_driver_with_mock_pool():
    pool = DbConnPool(connection_url="postgresql://test:test@localhost/db")
    pool._is_valid = True
    mock_async_pool = AsyncMock()
    pool.pool = mock_async_pool
    driver = SqlDriver(conn=pool)
    return driver, mock_async_pool


class TestTimeoutQueryWrapping:
    @pytest.mark.asyncio
    async def test_timeout_wraps_in_set_local(self):
        driver, mock_pool = _make_driver_with_mock_pool()

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.description = [("col1",)]
        mock_cursor.fetchall = AsyncMock(return_value=[{"col1": "val1"}])
        mock_cursor.nextset = MagicMock(return_value=False)

        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        mock_pool.connection = MagicMock(return_value=mock_conn)

        await driver.execute_query("SELECT 1", timeout_ms=5000)

        executed = [str(c) for c in mock_cursor.execute.call_args_list]
        assert any("statement_timeout" in s and "5000" in s for s in executed)

    @pytest.mark.asyncio
    async def test_no_timeout_skips_set_local(self):
        driver, mock_pool = _make_driver_with_mock_pool()

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.description = [("col1",)]
        mock_cursor.fetchall = AsyncMock(return_value=[{"col1": "val1"}])
        mock_cursor.nextset = MagicMock(return_value=False)

        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        mock_pool.connection = MagicMock(return_value=mock_conn)

        await driver.execute_query("SELECT 1")

        executed = [str(c) for c in mock_cursor.execute.call_args_list]
        assert not any("statement_timeout" in s for s in executed)

    @pytest.mark.asyncio
    async def test_timeout_zero_skips_set_local(self):
        driver, mock_pool = _make_driver_with_mock_pool()

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.description = [("col1",)]
        mock_cursor.fetchall = AsyncMock(return_value=[{"col1": "val1"}])
        mock_cursor.nextset = MagicMock(return_value=False)

        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        mock_pool.connection = MagicMock(return_value=mock_conn)

        await driver.execute_query("SELECT 1", timeout_ms=0)

        executed = [str(c) for c in mock_cursor.execute.call_args_list]
        assert not any("statement_timeout" in s for s in executed)


class TestTimeoutErrorHandling:
    @pytest.mark.asyncio
    async def test_query_canceled_raises(self):
        import psycopg.errors

        pool = DbConnPool(connection_url="postgresql://test:test@localhost/db")
        pool._is_valid = True

        call_count = 0

        async def mock_execute(sql, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")

        mock_cursor = MagicMock()
        mock_cursor.execute = mock_execute
        mock_cursor.nextset = MagicMock(return_value=False)
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        mock_conn.rollback = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        mock_async_pool = MagicMock()
        mock_async_pool.connection = MagicMock(return_value=mock_conn)
        pool.pool = mock_async_pool

        driver = SqlDriver(conn=pool)

        with pytest.raises(psycopg.errors.QueryCanceled):
            await driver.execute_query("SELECT pg_sleep(10)", timeout_ms=1000)


class TestServerDefaultTimeout:
    @pytest.mark.asyncio
    async def test_server_default_applied_when_no_per_query(self):
        driver, mock_pool = _make_driver_with_mock_pool()
        driver.default_timeout_ms = 3000

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.description = [("col1",)]
        mock_cursor.fetchall = AsyncMock(return_value=[{"col1": "val1"}])
        mock_cursor.nextset = MagicMock(return_value=False)

        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        mock_pool.connection = MagicMock(return_value=mock_conn)

        await driver.execute_query("SELECT 1")

        executed = [str(c) for c in mock_cursor.execute.call_args_list]
        assert any("statement_timeout" in s and "3000" in s for s in executed)

    @pytest.mark.asyncio
    async def test_per_query_overrides_server_default(self):
        driver, mock_pool = _make_driver_with_mock_pool()
        driver.default_timeout_ms = 3000

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.description = [("col1",)]
        mock_cursor.fetchall = AsyncMock(return_value=[{"col1": "val1"}])
        mock_cursor.nextset = MagicMock(return_value=False)

        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock()
        mock_pool.connection = MagicMock(return_value=mock_conn)

        await driver.execute_query("SELECT 1", timeout_ms=7000)

        executed = [str(c) for c in mock_cursor.execute.call_args_list]
        assert any("7000" in s for s in executed)
        assert not any("3000" in s for s in executed)
