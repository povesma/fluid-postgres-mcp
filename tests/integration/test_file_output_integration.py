"""Integration test: file export of 500K rows against real PostgreSQL on k8s."""

from __future__ import annotations

import csv
import os
import tempfile

import pytest

from postgres_mcp.sql.sql_driver import SqlDriver


@pytest.mark.asyncio
class TestFileExport500K:
    async def test_csv_file_valid_and_row_count_correct(self, k8s_pg_with_test_data: SqlDriver):
        driver = k8s_pg_with_test_data

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            result = await driver.execute_to_file(
                "SELECT id, value, amount FROM test_large ORDER BY id", path
            )

            assert result["rows"] == 500_000
            assert result["file"] == path
            assert result["columns"] == ["id", "value", "amount"]
            assert result["bytes"] > 0

            assert os.path.exists(path)
            file_size = os.path.getsize(path)
            assert file_size == result["bytes"]

            with open(path, newline="") as f:
                reader = csv.reader(f)
                header = next(reader)
                assert header == ["id", "value", "amount"]

                row_count = 0
                first_row = next(reader)
                assert first_row[0] == "1"
                assert first_row[1] == "row_1"
                row_count += 1

                for _ in reader:
                    row_count += 1

                assert row_count == 500_000
        finally:
            if os.path.exists(path):
                os.unlink(path)

    async def test_file_size_reasonable(self, k8s_pg_with_test_data: SqlDriver):
        driver = k8s_pg_with_test_data

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            result = await driver.execute_to_file(
                "SELECT id, value, amount FROM test_large", path
            )
            file_size_mb = result["bytes"] / (1024 * 1024)
            assert file_size_mb < 50, f"File size {file_size_mb:.1f} MB exceeds 50 MB limit"
            assert file_size_mb > 1, f"File size {file_size_mb:.1f} MB suspiciously small"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    async def test_progress_callback_invoked(self, k8s_pg_with_test_data: SqlDriver):
        driver = k8s_pg_with_test_data
        progress_calls: list[dict] = []

        def on_progress(rows: int, bytes_written: int, elapsed: float) -> None:
            progress_calls.append({"rows": rows, "bytes": bytes_written, "elapsed": elapsed})

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            await driver.execute_to_file(
                "SELECT id, value, amount FROM test_large", path,
                on_progress=on_progress,
            )
            assert len(progress_calls) >= 1, "Expected at least one progress callback for 500K rows"
            for call in progress_calls:
                assert call["rows"] > 0
                assert call["bytes"] > 0
                assert call["elapsed"] >= 0
        finally:
            if os.path.exists(path):
                os.unlink(path)

    async def test_empty_table_produces_header_only(self, k8s_sql_driver: SqlDriver):
        driver = k8s_sql_driver

        await driver.execute_query("CREATE TABLE IF NOT EXISTS test_empty (id int, name text)")
        await driver.execute_query("TRUNCATE test_empty")

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            result = await driver.execute_to_file(
                "SELECT id, name FROM test_empty", path
            )
            assert result["rows"] == 0
            assert result["columns"] == ["id", "name"]

            with open(path) as f:
                content = f.read().strip()
            assert content == "id,name"
        finally:
            if os.path.exists(path):
                os.unlink(path)
            await driver.execute_query("DROP TABLE IF EXISTS test_empty")
