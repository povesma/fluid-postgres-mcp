from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from postgres_mcp.config import ReconnectConfig
from postgres_mcp.sql.sql_driver import ConnState
from postgres_mcp.sql.sql_driver import DbConnPool


def _make_pool(script: str | None = "/bin/true", hook_timeout: float = 5.0, **kwargs):
    cfg = ReconnectConfig(
        pre_connect_script=script,
        hook_timeout=hook_timeout,
        initial_delay=0.01,
        **kwargs,
    )
    return DbConnPool(
        connection_url="postgresql://test:test@localhost/db",
        reconnect_config=cfg,
    )


def _patch_create_pool(pool):
    mock_pool = AsyncMock()

    async def fake_create_pool(url):
        return mock_pool

    pool._create_pool = fake_create_pool
    return mock_pool


class TestHookExecution:
    @pytest.mark.asyncio
    async def test_no_script_is_noop(self):
        pool = _make_pool(script=None)
        result = await pool._run_pre_connect_hook()
        assert result is True

    @pytest.mark.asyncio
    async def test_successful_script_returns_true(self):
        pool = _make_pool(script="echo hello")
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello\n", b""))
        mock_proc.returncode = 0

        with patch("postgres_mcp.sql.sql_driver.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await pool._run_pre_connect_hook()
        assert result is True

    @pytest.mark.asyncio
    async def test_failed_script_returns_false(self):
        pool = _make_pool(script="/bin/false")
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error\n"))
        mock_proc.returncode = 1

        with patch("postgres_mcp.sql.sql_driver.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await pool._run_pre_connect_hook()
        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_kills_script(self):
        pool = _make_pool(script="sleep 100", hook_timeout=0.01)
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=[asyncio.TimeoutError(), (b"", b"")])
        mock_proc.kill = AsyncMock()

        with patch("postgres_mcp.sql.sql_driver.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await pool._run_pre_connect_hook()
        assert result is False
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_in_hook_returns_false(self):
        pool = _make_pool(script="nonexistent_command_xyz")
        with patch(
            "postgres_mcp.sql.sql_driver.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("not found"),
        ):
            result = await pool._run_pre_connect_hook()
        assert result is False


class TestHookIntegration:
    @pytest.mark.asyncio
    async def test_hook_called_before_connect(self):
        pool = _make_pool(script="echo setup")
        call_order: list[str] = []

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        async def track_hook(*args, **kwargs):
            call_order.append("hook")
            return mock_proc

        async def track_create(url):
            call_order.append("connect")
            return AsyncMock()

        pool._create_pool = track_create

        with patch("postgres_mcp.sql.sql_driver.asyncio.create_subprocess_exec", side_effect=track_hook):
            await pool.pool_connect()

        assert call_order == ["hook", "connect"]

    @pytest.mark.asyncio
    async def test_hook_failure_skips_connect_in_reconnect(self):
        pool = _make_pool(script="/bin/false", max_attempts=1)
        pool._is_valid = False
        connect_called = False

        async def track_create(url):
            nonlocal connect_called
            connect_called = True
            return AsyncMock()

        pool._create_pool = track_create

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 1

        async def fast_sleep(d):
            pass

        with patch("postgres_mcp.sql.sql_driver.asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("postgres_mcp.sql.sql_driver.asyncio.sleep", side_effect=fast_sleep):
                with pytest.raises(ConnectionError):
                    await pool._reconnect_loop()

        assert not connect_called

    @pytest.mark.asyncio
    async def test_hook_failure_on_initial_connect(self):
        pool = _make_pool(script="/bin/false")
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 1

        with patch("postgres_mcp.sql.sql_driver.asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(ValueError, match="Pre-connect hook failed"):
                await pool.pool_connect()
        assert pool.state == ConnState.ERROR


class TestHookPathResolution:
    @pytest.mark.asyncio
    async def test_absolute_path(self):
        pool = _make_pool(script="/usr/bin/env echo hello")
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("postgres_mcp.sql.sql_driver.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await pool._run_pre_connect_hook()
        mock_exec.assert_called_once_with(
            "/usr/bin/env", "echo", "hello",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    @pytest.mark.asyncio
    async def test_executable_name_only(self):
        pool = _make_pool(script="my-tunnel-script")
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("postgres_mcp.sql.sql_driver.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await pool._run_pre_connect_hook()
        mock_exec.assert_called_once_with(
            "my-tunnel-script",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
