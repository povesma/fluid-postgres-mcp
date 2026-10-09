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
    async def test_hook_runs_before_connect(self, pg_connection_string):
        connection_string, _ = pg_connection_string

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

    async def test_hook_runs_on_reconnect(self, pg_connection_string):
        connection_string, _ = pg_connection_string

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

    async def test_failed_hook_prevents_connect(self, pg_connection_string):
        connection_string, _ = pg_connection_string

        with tempfile.NamedTemporaryFile(delete=False, suffix=".sh", mode="w") as script:
            script.write("#!/bin/sh\nexit 1\n")
            script_path = script.name
        os.chmod(script_path, stat.S_IRWXU)

        try:
            pool = DbConnPool(
                connection_url=connection_string,
                reconnect_config=ReconnectConfig(pre_connect_script=script_path),
            )
            with pytest.raises(ValueError, match=r"exited with code 1"):
                await pool.pool_connect()
            assert pool.state == ConnState.ERROR

            await pool.close()
        finally:
            os.unlink(script_path)

    async def test_no_hook_configured_is_noop(self, pg_connection_string):
        connection_string, _ = pg_connection_string

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


def _write_script(path: str, body: str) -> None:
    with open(path, "w") as f:
        f.write("#!/bin/bash\n" + body)
    os.chmod(path, stat.S_IRWXU)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
class TestRealScriptTeardown:
    """Real processes, no database: ConnectionScriptManager teardown on POSIX."""

    async def test_sigterm_handler_runs_on_stop(self, tmp_path):
        from postgres_mcp.sql.connection_script import ConnectionScriptManager

        marker = tmp_path / "terminated"
        pid_file = tmp_path / "pid"
        child_pid_file = tmp_path / "child_pid"
        script = tmp_path / "s.sh"
        _write_script(
            str(script),
            f"trap 'echo done > \"{marker}\"; exit 0' TERM\n"
            f'echo $$ > "{pid_file}"\n'
            'echo "[MCP] READY_TO_CONNECT"\n'
            "sleep 600 &\n"
            f'echo $! > "{child_pid_file}"\n'
            "wait $!\n",
        )
        events: list[str] = []
        mgr = ConnectionScriptManager(script=str(script), hook_timeout=5.0, on_event=events.append)
        try:
            assert (await mgr.ensure_ready()).success is True
            await mgr.stop()

            assert marker.read_text().strip() == "done"
            assert not _pid_alive(int(pid_file.read_text()))
            assert not any("force-killed" in e for e in events)
        finally:
            if child_pid_file.exists():
                child_pid = int(child_pid_file.read_text())
                if _pid_alive(child_pid):
                    os.kill(child_pid, 9)

    async def test_script_ignoring_sigterm_is_killed_after_grace(self, tmp_path, monkeypatch):
        import time

        from postgres_mcp.sql import connection_script
        from postgres_mcp.sql.connection_script import ConnectionScriptManager

        monkeypatch.setattr(connection_script, "_TERMINATE_GRACE_S", 0.5)
        pid_file = tmp_path / "pid"
        child_pid_file = tmp_path / "child_pid"
        script = tmp_path / "s.sh"
        _write_script(
            str(script),
            "trap '' TERM\n"
            f'echo $$ > "{pid_file}"\n'
            'echo "[MCP] READY_TO_CONNECT"\n'
            "sleep 600 &\n"
            f'echo $! > "{child_pid_file}"\n'
            "wait $!\n",
        )
        mgr = ConnectionScriptManager(script=str(script), hook_timeout=5.0)
        try:
            assert (await mgr.ensure_ready()).success is True
            started = time.monotonic()
            await mgr.stop()
            elapsed = time.monotonic() - started

            assert elapsed < 0.5 + 1.0
            assert not _pid_alive(int(pid_file.read_text()))
        finally:
            if child_pid_file.exists():
                child_pid = int(child_pid_file.read_text())
                if _pid_alive(child_pid):
                    os.kill(child_pid, 9)

    async def test_exit_detected_while_child_holds_stdout(self, tmp_path):
        import asyncio

        from postgres_mcp.sql.connection_script import ConnectionScriptManager

        child_pid_file = tmp_path / "child_pid"
        script = tmp_path / "s.sh"
        _write_script(
            str(script),
            "sleep 600 &\n"
            f'echo $! > "{child_pid_file}"\n'
            'echo "[MCP] READY_TO_CONNECT"\n'
            "sleep 0.3\n"
            "exit 3\n",
        )
        events: list[str] = []
        mgr = ConnectionScriptManager(script=str(script), hook_timeout=5.0, on_event=events.append)
        try:
            assert (await mgr.ensure_ready()).success is True

            async def _exited_event() -> None:
                while not any("Pre-connect-script exited" in e for e in events):
                    await asyncio.sleep(0.05)

            await asyncio.wait_for(_exited_event(), timeout=2.0)
            assert any("exited (code=3)" in e for e in events)
        finally:
            await mgr.stop()
            if child_pid_file.exists():
                child_pid = int(child_pid_file.read_text())
                if _pid_alive(child_pid):
                    os.kill(child_pid, 9)

    async def test_quoted_path_with_space_starts(self, tmp_path):
        from postgres_mcp.sql.connection_script import ConnectionScriptManager
        from postgres_mcp.sql.connection_script import ScriptMode

        script_dir = tmp_path / "dir with space"
        script_dir.mkdir()
        marker = tmp_path / "ran"
        script = script_dir / "s.sh"
        _write_script(str(script), f'echo "$1" > "{marker}"\nexit 0\n')

        mgr = ConnectionScriptManager(script=f'"{script}" mydb', hook_timeout=5.0)
        outcome = await mgr.ensure_ready()

        assert outcome.success is True
        assert outcome.mode is ScriptMode.RUN_AND_EXIT
        assert marker.read_text().strip() == "mydb"
