"""Pre-connect hook integration tests via DbConnPool.

Direct-call coverage of the now-removed `_run_pre_connect_hook` lives
in `test_connection_script.py`. This file keeps the
DbConnPool-level integration tests: hook-before-connect ordering,
reconnect-loop short-circuit on hook failure, initial-connect
failure-mode propagation.
"""

from __future__ import annotations

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


class TestHookIntegration:
    @pytest.mark.asyncio
    async def test_hook_called_before_connect(self):
        pool = _make_pool(script="echo setup")
        call_order: list[str] = []

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0
        # async-iterable empty stdout — exits immediately, RUN_AND_EXIT.
        mock_proc.stdout = _empty_async_iter()
        mock_proc.stderr = _empty_async_iter()

        async def track_spawn(*args, **kwargs):
            call_order.append("hook")
            return mock_proc

        async def track_create(url):
            call_order.append("connect")
            return AsyncMock()

        pool._create_pool = track_create

        with patch("asyncio.create_subprocess_exec", side_effect=track_spawn):
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
        mock_proc.wait = AsyncMock(return_value=1)
        mock_proc.returncode = 1
        mock_proc.stdout = _empty_async_iter()
        mock_proc.stderr = _empty_async_iter()

        async def fast_sleep(d):
            return None

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.sleep", side_effect=fast_sleep):
                with pytest.raises(ConnectionError):
                    await pool._reconnect_loop()

        assert not connect_called

    @pytest.mark.asyncio
    async def test_hook_failure_on_initial_connect(self):
        pool = _make_pool(script="/bin/false")
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.wait = AsyncMock(return_value=1)
        mock_proc.returncode = 1
        mock_proc.stdout = _empty_async_iter()
        mock_proc.stderr = _empty_async_iter()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(ValueError, match="exited with code 1"):
                await pool.pool_connect()
        assert pool.state == ConnState.ERROR


def _empty_async_iter():
    """Async-iterable that yields no lines and immediately raises StopAsyncIteration."""

    class _Empty:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    return _Empty()
