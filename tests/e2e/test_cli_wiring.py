"""E2E test: CLI args flow through parse_config into runtime behavior."""

from __future__ import annotations

import os
import stat
import tempfile

import pytest

from mcp_client_fixtures import call_tool
from mcp_client_fixtures import create_mcp_session
from mcp_client_fixtures import extract_text


@pytest.mark.asyncio
class TestPreConnectScriptArg:
    async def test_marker_file_created_on_boot(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        with tempfile.NamedTemporaryFile(delete=False, suffix=".marker") as marker:
            marker_path = marker.name
        os.unlink(marker_path)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sh", mode="w") as script:
            script.write(f"#!/bin/sh\ntouch {marker_path}\n")
            script_path = script.name
        os.chmod(script_path, stat.S_IRWXU)

        try:
            async for session in create_mcp_session(
                connection_string,
                extra_args=["--pre-connect-script", script_path],
            ):
                assert os.path.exists(marker_path), (
                    "--pre-connect-script did not execute: marker file not created"
                )
                result = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS ok"})
                assert not result.isError
                assert "1" in extract_text(result)
        finally:
            for p in (marker_path, script_path):
                if os.path.exists(p):
                    os.unlink(p)


@pytest.mark.asyncio
class TestDefaultTimeoutArg:
    async def test_server_default_timeout_cancels_slow_query(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(
            connection_string,
            extra_args=["--default-timeout", "1000"],
        ):
            result = await call_tool(session, "execute_sql", {"sql": "SELECT pg_sleep(10)"})
            assert result.isError, "Expected isError=True for timed-out query"
            text = extract_text(result)
            assert "timeout" in text.lower() or "cancel" in text.lower(), (
                f"Expected timeout error message, got: {text}"
            )

    async def test_fast_query_succeeds_within_timeout(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(
            connection_string,
            extra_args=["--default-timeout", "5000"],
        ):
            result = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS ok"})
            assert not result.isError
            assert "1" in extract_text(result)


@pytest.mark.asyncio
class TestOutputDirArg:
    async def test_file_created_in_output_dir(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        with tempfile.TemporaryDirectory() as tmpdir:
            async for session in create_mcp_session(
                connection_string,
                extra_args=["--output-dir", tmpdir],
            ):
                result = await call_tool(session, "execute_sql", {
                    "sql": "SELECT generate_series(1, 10) AS id",
                    "output_file": "test_output.csv",
                    "output_mode": "file",
                })
                assert not result.isError
                text = extract_text(result)

                expected_path = os.path.join(tmpdir, "test_output.csv")
                assert os.path.exists(expected_path), (
                    f"--output-dir not honored: expected file at {expected_path}"
                )
                assert expected_path in text


@pytest.mark.asyncio
class TestEventBufferSizeArg:
    async def test_buffer_limits_events(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        async for session in create_mcp_session(
            connection_string,
            extra_args=["--event-buffer-size", "3"],
        ):
            for i in range(10):
                await call_tool(session, "execute_sql", {"sql": f"SELECT {i}"})

            result = await call_tool(session, "status", {"events": 100})
            text = extract_text(result)
            parsed = eval(text)
            events = parsed.get("events", [])
            assert len(events) <= 3, (
                f"--event-buffer-size=3 not honored: got {len(events)} events"
            )
