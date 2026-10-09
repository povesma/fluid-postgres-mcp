"""E2E test: server lifecycle — bad connection, graceful shutdown."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time

import pytest

from mcp_client_fixtures import call_tool
from mcp_client_fixtures import create_mcp_session
from mcp_client_fixtures import extract_text


BAD_URL = "postgresql://user:pass@192.0.2.1:5432/nonexistent?connect_timeout=2"
BAD_CONN_ARGS = [
    "--reconnect-max-attempts", "2",
    "--reconnect-initial-delay", "0.5",
    "--reconnect-max-delay", "1",
]


@pytest.mark.asyncio
class TestBadConnectionString:
    async def test_server_stays_alive_with_unreachable_host(self):
        async for session in create_mcp_session(BAD_URL, extra_args=BAD_CONN_ARGS):
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            assert "execute_sql" in tool_names
            assert "status" in tool_names

    async def test_execute_sql_returns_error_with_bad_connection(self):
        async for session in create_mcp_session(BAD_URL, extra_args=BAD_CONN_ARGS):
            result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
            assert result.isError
            text = extract_text(result)
            assert len(text) > 0

    async def test_status_shows_state_with_bad_connection(self):
        async for session in create_mcp_session(BAD_URL, extra_args=BAD_CONN_ARGS):
            result = await call_tool(session, "status", {"events": 10})
            assert not result.isError
            text = extract_text(result)
            parsed = eval(text)
            assert parsed["state"] in ("disconnected", "error", "reconnecting")


@pytest.mark.asyncio
class TestGracefulShutdown:
    async def test_sigterm_exits_cleanly(self):
        dummy_url = "postgresql://user:pass@192.0.2.1:5432/db?connect_timeout=1"

        proc = subprocess.Popen(
            [sys.executable, "-m", "postgres_mcp", dummy_url],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": "src"},
        )

        time.sleep(3)
        assert proc.poll() is None, "Server died before we could send SIGTERM"

        proc.send_signal(signal.SIGTERM)

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail("Server did not exit within 10s after SIGTERM")

    async def test_server_responds_then_shuts_down(self, pg_connection_string):
        connection_string, _ = pg_connection_string

        async for session in create_mcp_session(connection_string):
            result = await call_tool(session, "execute_sql", {"sql": "SELECT 'alive' AS s"})
            assert not result.isError
            assert "alive" in extract_text(result)


# ---------------------------------------------------------------------------
# The pre-connect script is stopped whenever the MCP exits (FR-12)
# ---------------------------------------------------------------------------

_UNREACHABLE_URL = "postgresql://u:p@192.0.2.1:5432/db?connect_timeout=1"


def _write_tunnel_script(path, pid_file, db_url=None):
    """Long-running script. Without `db_url` the MCP starts in
    WAITING_FOR_URL and reaches the transport at once; with an unreachable
    `db_url` it sits in the initial connect (psycopg pool timeout)."""
    url_line = f'echo "[MCP] DB_URL {db_url}"\n' if db_url else ""
    path.write_text(
        "#!/bin/bash\n"
        f'echo $$ > "{pid_file}"\n'
        f"{url_line}"
        'echo "[MCP] READY_TO_CONNECT"\n'
        "sleep 600 &\n"
        "SLEEP=$!\n"
        "trap 'kill $SLEEP; exit 0' TERM\n"
        "wait $SLEEP\n"
    )
    path.chmod(0o700)


def _wait_for(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _start_mcp(script, extra_args=()):
    return subprocess.Popen(
        [sys.executable, "-m", "postgres_mcp", "--pre-connect-script", str(script), *extra_args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": "src"},
    )


class TestScriptStoppedOnExit:
    def test_closing_stdin_stops_the_script(self, tmp_path):
        pid_file = tmp_path / "script.pid"
        script = tmp_path / "tunnel.sh"
        _write_tunnel_script(script, pid_file)
        proc = _start_mcp(script)
        try:
            assert _wait_for(pid_file.exists, 20), "script never started"
            script_pid = int(pid_file.read_text())
            time.sleep(1)
            proc.stdin.close()
            proc.wait(timeout=15)
            assert _wait_for(lambda: not _pid_alive(script_pid), 7), "script still running after MCP exit"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_sigterm_on_streamable_http_stops_the_script(self, tmp_path):
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        pid_file = tmp_path / "script.pid"
        script = tmp_path / "tunnel.sh"
        _write_tunnel_script(script, pid_file)
        proc = _start_mcp(script, ["--transport", "streamable-http", "--streamable-http-port", str(port)])
        try:
            assert _wait_for(pid_file.exists, 20), "script never started"
            script_pid = int(pid_file.read_text())
            time.sleep(2)
            proc.send_signal(signal.SIGTERM)
            code = proc.wait(timeout=15)
            assert code == 128 + signal.SIGTERM
            assert _wait_for(lambda: not _pid_alive(script_pid), 7), "script still running after MCP exit"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_sigterm_during_initial_connect_stops_the_script(self, tmp_path):
        pid_file = tmp_path / "script.pid"
        script = tmp_path / "tunnel.sh"
        _write_tunnel_script(script, pid_file, db_url=_UNREACHABLE_URL)
        proc = _start_mcp(script)
        try:
            assert _wait_for(pid_file.exists, 20), "script never started"
            script_pid = int(pid_file.read_text())
            time.sleep(1)
            proc.send_signal(signal.SIGTERM)
            code = proc.wait(timeout=15)
            assert code == 128 + signal.SIGTERM
            assert _wait_for(lambda: not _pid_alive(script_pid), 7), "script still running after MCP exit"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
