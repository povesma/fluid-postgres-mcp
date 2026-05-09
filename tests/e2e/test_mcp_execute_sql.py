"""E2E test: execute_sql tool via MCP protocol in all 3 output modes."""

from __future__ import annotations

import os
import tempfile

import pytest

from mcp_client_fixtures import call_tool
from mcp_client_fixtures import create_mcp_session
from mcp_client_fixtures import extract_text


@pytest.mark.asyncio
class TestInlineMode:
    async def test_returns_rows(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            result = await call_tool(session, "execute_sql", {
                "sql": "SELECT id, 'row_' || id AS name FROM generate_series(1, 5) AS id",
            })
            assert not result.isError
            text = extract_text(result)
            assert "row_1" in text
            assert "row_5" in text

    async def test_no_results_query(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            result = await call_tool(session, "execute_sql", {
                "sql": "CREATE TEMP TABLE _test_noop (id int)",
            })
            assert not result.isError

    async def test_error_sets_isError(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            result = await call_tool(session, "execute_sql", {
                "sql": "SELECT * FROM nonexistent_table_xyz",
            })
            assert result.isError
            text = extract_text(result)
            assert "nonexistent_table_xyz" in text


@pytest.mark.asyncio
class TestFileMode:
    async def test_creates_csv_with_metadata(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "output.csv")

            async for session in create_mcp_session(connection_string):
                result = await call_tool(session, "execute_sql", {
                    "sql": "SELECT id, 'val_' || id AS name FROM generate_series(1, 100) AS id",
                    "output_file": csv_path,
                    "output_mode": "file",
                })
                assert not result.isError
                text = extract_text(result)
                assert "100" in text
                assert "rows" in text or "row" in text.lower()
                assert csv_path in text

                assert os.path.exists(csv_path)
                with open(csv_path) as f:
                    lines = f.readlines()
                assert lines[0].strip() == "id,name"
                assert len(lines) == 101

    async def test_file_mode_no_inline_data(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "no_inline.csv")

            async for session in create_mcp_session(connection_string):
                result = await call_tool(session, "execute_sql", {
                    "sql": "SELECT generate_series(1, 50) AS id",
                    "output_file": csv_path,
                    "output_mode": "file",
                })
                assert not result.isError
                text = extract_text(result)
                parsed = eval(text)
                assert "data" not in parsed
                assert "file" in parsed
                assert parsed["rows"] == 50


@pytest.mark.asyncio
class TestFileInlineMode:
    async def test_returns_both_file_and_data(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "both.csv")

            async for session in create_mcp_session(connection_string):
                result = await call_tool(session, "execute_sql", {
                    "sql": "SELECT id, 'item_' || id AS name FROM generate_series(1, 10) AS id",
                    "output_file": csv_path,
                    "output_mode": "file+inline",
                })
                assert not result.isError
                text = extract_text(result)
                parsed = eval(text)

                assert "file_metadata" in parsed
                assert parsed["file_metadata"]["rows"] == 10
                assert os.path.exists(csv_path)

                assert "data" in parsed
                assert len(parsed["data"]) == 10
                assert parsed["data"][0]["name"] == "item_1"


@pytest.mark.asyncio
class TestPerQueryTimeout:
    async def test_per_query_timeout_overrides_default(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(
            connection_string,
            extra_args=["--default-timeout", "30000"],
        ):
            result = await call_tool(session, "execute_sql", {
                "sql": "SELECT pg_sleep(10)",
                "timeout_ms": 500,
            })
            assert result.isError
            text = extract_text(result)
            assert "timeout" in text.lower() or "cancel" in text.lower()

    async def test_connection_usable_after_error(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(connection_string):
            await call_tool(session, "execute_sql", {
                "sql": "SELECT pg_sleep(10)",
                "timeout_ms": 500,
            })

            result = await call_tool(session, "execute_sql", {
                "sql": "SELECT 'recovered' AS status",
            })
            assert not result.isError
            assert "recovered" in extract_text(result)
