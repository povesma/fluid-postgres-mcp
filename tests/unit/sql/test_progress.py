from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from postgres_mcp.sql.sql_driver import DbConnPool
from postgres_mcp.sql.sql_driver import SqlDriver


class FakeCopy:
    def __init__(self, blocks, statusmessage="COPY 0"):
        self._blocks = blocks
        self.statusmessage = statusmessage

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._blocks:
            raise StopAsyncIteration
        return self._blocks.pop(0)


class FakeCursor:
    def __init__(self, copy_obj):
        self._copy = copy_obj
        self.statusmessage = copy_obj.statusmessage
        self._execute_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, *a, **kw):
        self._execute_calls.append(str(sql))

    def copy(self, sql):
        self._execute_calls.append(str(sql))
        return self._copy


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def cursor(self, **kw):
        return self._cursor

    async def rollback(self):
        pass


def _make_driver():
    pool = DbConnPool(connection_url="postgresql://test:test@localhost/db")
    pool._is_valid = True
    mock_pool = MagicMock()
    pool.pool = mock_pool
    driver = SqlDriver(conn=pool)
    return driver, mock_pool


class TestProgressCallback:
    @pytest.mark.asyncio
    async def test_callback_receives_rows_bytes_elapsed(self):
        blocks = [b"id\n" + b"x\n" * 200000]
        copy = FakeCopy(blocks, statusmessage="COPY 200000")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        calls: list[tuple] = []

        def on_progress(rows, bytes_written, elapsed):
            calls.append((rows, bytes_written, elapsed))

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            await driver.execute_to_file("SELECT 1", path, on_progress=on_progress)
            assert len(calls) >= 1
            rows, bw, elapsed = calls[0]
            assert rows > 0
            assert bw > 0
            assert elapsed >= 0
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_no_callback_when_none(self):
        blocks = [b"id\n" + b"x\n" * 200000]
        copy = FakeCopy(blocks, statusmessage="COPY 200000")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            result = await driver.execute_to_file("SELECT 1", path, on_progress=None)
            assert result["rows"] == 200000
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_small_result_no_progress(self):
        blocks = [b"id\n1\n2\n"]
        copy = FakeCopy(blocks, statusmessage="COPY 2")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        calls: list[tuple] = []

        def on_progress(rows, bytes_written, elapsed):
            calls.append((rows, bytes_written, elapsed))

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            await driver.execute_to_file("SELECT 1", path, on_progress=on_progress)
            assert len(calls) == 0
        finally:
            os.unlink(path)


class TestInlineModeNoProgress:
    @pytest.mark.asyncio
    async def test_inline_execute_has_no_progress_mechanism(self):
        """execute_query (inline) doesn't accept on_progress — progress is file-only."""
        import inspect
        sig = inspect.signature(SqlDriver.execute_query)
        assert "on_progress" not in sig.parameters
