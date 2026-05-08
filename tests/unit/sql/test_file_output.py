from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from postgres_mcp.sql.sql_driver import DbConnPool
from postgres_mcp.sql.sql_driver import SqlDriver


def _make_driver():
    pool = DbConnPool(connection_url="postgresql://test:test@localhost/db")
    pool._is_valid = True
    mock_pool = MagicMock()
    pool.pool = mock_pool
    driver = SqlDriver(conn=pool)
    return driver, mock_pool


class FakeCopy:
    """Simulates psycopg3 AsyncCopy with COPY TO STDOUT blocks."""

    def __init__(self, blocks: list[bytes], statusmessage: str = "COPY 3"):
        self._blocks = blocks
        self.statusmessage = statusmessage

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._blocks:
            raise StopAsyncIteration
        return self._blocks.pop(0)


class FakeCursor:
    def __init__(self, copy_obj: FakeCopy):
        self._copy = copy_obj
        self.statusmessage = copy_obj.statusmessage
        self._execute_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, sql, *args, **kwargs):
        self._execute_calls.append(str(sql))

    def copy(self, sql):
        self._execute_calls.append(str(sql))
        return self._copy


class FakeConn:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def cursor(self, **kwargs):
        return self._cursor

    async def rollback(self):
        pass


class TestExecuteToFile:
    @pytest.mark.asyncio
    async def test_creates_file_with_csv_data(self):
        blocks = [
            b"id,name,value\n",
            b"1,alice,100\n2,bob,200\n",
            b"3,charlie,300\n",
        ]
        copy = FakeCopy(blocks, statusmessage="COPY 3")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            result = await driver.execute_to_file("SELECT * FROM t", path)
            assert result["rows"] == 3
            assert result["bytes"] > 0
            assert result["file"] == path
            assert result["columns"] == ["id", "name", "value"]

            with open(path) as f:
                content = f.read()
            assert "id,name,value" in content
            assert "alice" in content
            assert "charlie" in content
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_empty_result_produces_header_only(self):
        blocks = [b"id,name\n"]
        copy = FakeCopy(blocks, statusmessage="COPY 0")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            result = await driver.execute_to_file("SELECT * FROM t WHERE 1=0", path)
            assert result["rows"] == 0
            assert result["columns"] == ["id", "name"]

            with open(path) as f:
                content = f.read()
            assert "id,name" in content
            lines = [l for l in content.strip().split("\n") if l]
            assert len(lines) == 1
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_byte_count_accurate(self):
        data = b"id,val\n1,abc\n2,def\n"
        blocks = [data]
        copy = FakeCopy(blocks, statusmessage="COPY 2")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            result = await driver.execute_to_file("SELECT 1", path)
            assert result["bytes"] == len(data)
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_row_count_from_statusmessage(self):
        blocks = [b"a\n" + b"x\n" * 500]
        copy = FakeCopy(blocks, statusmessage="COPY 500")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            result = await driver.execute_to_file("SELECT 1", path)
            assert result["rows"] == 500
        finally:
            os.unlink(path)


class TestColumnExtraction:
    @pytest.mark.asyncio
    async def test_columns_from_csv_header(self):
        blocks = [b"user_id,email,created_at\n1,a@b.com,2026-01-01\n"]
        copy = FakeCopy(blocks, statusmessage="COPY 1")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            result = await driver.execute_to_file("SELECT 1", path)
            assert result["columns"] == ["user_id", "email", "created_at"]
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_columns_with_spaces_in_names(self):
        blocks = [b'"first name","last name"\nAlice,Smith\n']
        copy = FakeCopy(blocks, statusmessage="COPY 1")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            result = await driver.execute_to_file("SELECT 1", path)
            assert result["columns"] == ["first name", "last name"]
        finally:
            os.unlink(path)


class TestProgressCallback:
    @pytest.mark.asyncio
    async def test_on_progress_called(self):
        blocks = [b"id\n" + b"x\n" * 200000]
        copy = FakeCopy(blocks, statusmessage="COPY 200000")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        progress_calls: list[dict] = []

        def on_progress(rows, bytes_written, elapsed):
            progress_calls.append({"rows": rows, "bytes": bytes_written})

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            await driver.execute_to_file("SELECT 1", path, on_progress=on_progress)
            assert len(progress_calls) >= 1
        finally:
            os.unlink(path)


class TestTimeoutOnFileOutput:
    @pytest.mark.asyncio
    async def test_timeout_applies_set_local_in_copy(self):
        blocks = [b"id\n1\n"]
        copy = FakeCopy(blocks, statusmessage="COPY 1")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            await driver.execute_to_file("SELECT 1", path, timeout_ms=5000)
            assert any("BEGIN" in c for c in cursor._execute_calls)
            assert any("statement_timeout" in c and "5000" in c for c in cursor._execute_calls)
            assert any("COMMIT" in c for c in cursor._execute_calls)
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_no_timeout_skips_transaction(self):
        blocks = [b"id\n1\n"]
        copy = FakeCopy(blocks, statusmessage="COPY 1")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            await driver.execute_to_file("SELECT 1", path)
            assert not any("BEGIN" in c for c in cursor._execute_calls)
        finally:
            os.unlink(path)


class TestOutputDir:
    @pytest.mark.asyncio
    async def test_relative_path_resolved_against_output_dir(self):
        blocks = [b"id\n1\n"]
        copy = FakeCopy(blocks, statusmessage="COPY 1")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await driver.execute_to_file(
                "SELECT 1", "output.csv", output_dir=tmpdir
            )
            expected_path = os.path.join(tmpdir, "output.csv")
            assert result["file"] == expected_path
            assert os.path.exists(expected_path)

    @pytest.mark.asyncio
    async def test_absolute_path_ignores_output_dir(self):
        blocks = [b"id\n1\n"]
        copy = FakeCopy(blocks, statusmessage="COPY 1")
        cursor = FakeCursor(copy)
        conn = FakeConn(cursor)

        driver, mock_pool = _make_driver()
        mock_pool.connection = MagicMock(return_value=conn)

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            abs_path = f.name

        try:
            result = await driver.execute_to_file(
                "SELECT 1", abs_path, output_dir="/some/other/dir"
            )
            assert result["file"] == abs_path
        finally:
            os.unlink(abs_path)
