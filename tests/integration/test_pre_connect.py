"""Integration test: pre-connect hook script executes before connection."""

from __future__ import annotations

import os
import stat
import tempfile

import pytest
import pytest_asyncio

from postgres_mcp.config import ReconnectConfig
from postgres_mcp.sql.sql_driver import ConnState
from postgres_mcp.sql.sql_driver import DbConnPool
from postgres_mcp.sql.sql_driver import SqlDriver


@pytest.mark.asyncio
class TestPreConnectHookIntegration:
    async def test_hook_runs_before_connect(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        with tempfile.NamedTemporaryFile(delete=False, suffix=".marker") as marker:
            marker_path = marker.name
        os.unlink(marker_path)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sh", mode="w") as script:
            script.write(f"#!/bin/sh\ntouch {marker_path}\n")
            script_path = script.name
        os.chmod(script_path, stat.S_IRWXU)

        try:
            pool = DbConnPool(
                connection_url=connection_string,
                reconnect_config=ReconnectConfig(pre_connect_script=script_path),
            )
            await pool.pool_connect()

            assert os.path.exists(marker_path), "Hook marker file was not created"
            assert pool.state == ConnState.CONNECTED

            driver = SqlDriver(conn=pool)
            result = await driver.execute_query("SELECT 1 AS check")
            assert result[0].cells["check"] == 1

            await pool.close()
        finally:
            for p in (marker_path, script_path):
                if os.path.exists(p):
                    os.unlink(p)

    async def test_hook_runs_on_reconnect(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        with tempfile.NamedTemporaryFile(delete=False, suffix=".counter", mode="w") as counter_file:
            counter_file.write("0")
            counter_path = counter_file.name

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sh", mode="w") as script:
            script.write(
                f'#!/bin/sh\n'
                f'count=$(cat {counter_path})\n'
                f'count=$((count + 1))\n'
                f'echo $count > {counter_path}\n'
            )
            script_path = script.name
        os.chmod(script_path, stat.S_IRWXU)

        try:
            pool = DbConnPool(
                connection_url=connection_string,
                reconnect_config=ReconnectConfig(
                    pre_connect_script=script_path,
                    initial_delay=0.5,
                    max_delay=2.0,
                    max_attempts=5,
                ),
            )
            await pool.pool_connect()

            with open(counter_path) as f:
                assert int(f.read().strip()) == 1

            pool.mark_invalid("test: forcing reconnect")
            driver = SqlDriver(conn=pool)
            await driver.execute_query("SELECT 1")

            with open(counter_path) as f:
                assert int(f.read().strip()) == 2

            await pool.close()
        finally:
            for p in (counter_path, script_path):
                if os.path.exists(p):
                    os.unlink(p)

    async def test_failed_hook_prevents_connect(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sh", mode="w") as script:
            script.write("#!/bin/sh\nexit 1\n")
            script_path = script.name
        os.chmod(script_path, stat.S_IRWXU)

        try:
            pool = DbConnPool(
                connection_url=connection_string,
                reconnect_config=ReconnectConfig(pre_connect_script=script_path),
            )
            with pytest.raises(ValueError, match="Pre-connect hook failed"):
                await pool.pool_connect()
            assert pool.state == ConnState.ERROR

            await pool.close()
        finally:
            os.unlink(script_path)

    async def test_no_hook_configured_is_noop(self, k8s_pg_connection_string):
        connection_string, _ = k8s_pg_connection_string

        pool = DbConnPool(
            connection_url=connection_string,
            reconnect_config=ReconnectConfig(),
        )
        await pool.pool_connect()
        assert pool.state == ConnState.CONNECTED

        driver = SqlDriver(conn=pool)
        result = await driver.execute_query("SELECT 1 AS ok")
        assert result[0].cells["ok"] == 1

        await pool.close()
