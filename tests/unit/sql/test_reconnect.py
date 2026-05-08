from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from postgres_mcp.config import ReconnectConfig
from postgres_mcp.sql.sql_driver import ConnState
from postgres_mcp.sql.sql_driver import DbConnPool


@pytest.fixture
def pool_factory():
    def _make(**kwargs):
        cfg = ReconnectConfig(**kwargs)
        return DbConnPool(connection_url="postgresql://test:test@localhost/db", reconnect_config=cfg)
    return _make


def _patch_create_pool(pool, succeed=True, fail_times=0):
    """Patch _create_pool to succeed or fail without real connections."""
    call_count = 0
    mock_pool = AsyncMock()

    async def fake_create_pool(url):
        nonlocal call_count
        call_count += 1
        if not succeed or call_count <= fail_times:
            raise ConnectionError("refused")
        return mock_pool

    pool._create_pool = fake_create_pool
    return mock_pool


class TestConnState:
    def test_initial_state_is_disconnected(self, pool_factory):
        pool = pool_factory()
        assert pool.state == ConnState.DISCONNECTED

    def test_reconnect_count_starts_at_zero(self, pool_factory):
        pool = pool_factory()
        assert pool.reconnect_count == 0


class TestPoolConnect:
    @pytest.mark.asyncio
    async def test_successful_connect(self, pool_factory):
        pool = pool_factory()
        mock_pool = _patch_create_pool(pool)
        result = await pool.pool_connect()
        assert result is mock_pool
        assert pool.state == ConnState.CONNECTED
        assert pool.is_valid

    @pytest.mark.asyncio
    async def test_connect_failure_sets_error_state(self, pool_factory):
        pool = pool_factory()
        _patch_create_pool(pool, succeed=False)
        with pytest.raises(ValueError, match="Connection attempt failed"):
            await pool.pool_connect()
        assert pool.state == ConnState.ERROR
        assert not pool.is_valid

    @pytest.mark.asyncio
    async def test_no_url_raises(self):
        pool = DbConnPool()
        with pytest.raises(ValueError, match="not provided"):
            await pool.pool_connect()


class TestReconnectLoop:
    @pytest.mark.asyncio
    async def test_successful_reconnect(self, pool_factory):
        pool = pool_factory(initial_delay=0.01, max_delay=0.02)
        pool._is_valid = False
        mock_pool = _patch_create_pool(pool)
        result = await pool._reconnect_loop()
        assert result is mock_pool
        assert pool.state == ConnState.CONNECTED
        assert pool.reconnect_count == 1

    @pytest.mark.asyncio
    async def test_max_attempts_exhaustion(self, pool_factory):
        pool = pool_factory(initial_delay=0.01, max_delay=0.02, max_attempts=2)
        pool._is_valid = False
        _patch_create_pool(pool, succeed=False)
        with pytest.raises(ConnectionError, match="failed after 2 attempts"):
            await pool._reconnect_loop()
        assert pool.state == ConnState.ERROR

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self, pool_factory):
        pool = pool_factory(initial_delay=0.01, max_delay=0.04, max_attempts=3)
        pool._is_valid = False
        _patch_create_pool(pool, succeed=False)

        delays: list[float] = []

        async def capture_sleep(d):
            delays.append(d)

        with patch("postgres_mcp.sql.sql_driver.asyncio.sleep", side_effect=capture_sleep):
            with pytest.raises(ConnectionError):
                await pool._reconnect_loop()

        assert len(delays) >= 2
        assert delays[0] == pytest.approx(0.01)
        assert delays[1] == pytest.approx(0.02)

    @pytest.mark.asyncio
    async def test_state_transitions_through_reconnecting(self, pool_factory):
        pool = pool_factory(initial_delay=0.01, max_attempts=1)
        pool._is_valid = False
        _patch_create_pool(pool, succeed=False)

        states: list[ConnState] = []

        async def capture_state(d):
            states.append(pool.state)

        with patch("postgres_mcp.sql.sql_driver.asyncio.sleep", side_effect=capture_state):
            with pytest.raises(ConnectionError):
                await pool._reconnect_loop()

        assert ConnState.RECONNECTING in states

    @pytest.mark.asyncio
    async def test_reconnect_after_transient_failure(self, pool_factory):
        pool = pool_factory(initial_delay=0.01)
        pool._is_valid = False
        mock_pool = _patch_create_pool(pool, succeed=True, fail_times=2)

        async def fast_sleep(d):
            pass

        with patch("postgres_mcp.sql.sql_driver.asyncio.sleep", side_effect=fast_sleep):
            result = await pool._reconnect_loop()
        assert result is mock_pool
        assert pool.reconnect_count == 1

    @pytest.mark.asyncio
    async def test_no_url_raises_in_reconnect(self):
        pool = DbConnPool()
        pool._is_valid = False
        with pytest.raises(ValueError, match="No connection URL"):
            await pool._reconnect_loop()


class TestMarkInvalid:
    def test_mark_invalid_sets_state(self, pool_factory):
        pool = pool_factory()
        pool._is_valid = True
        pool._state = ConnState.CONNECTED
        pool.mark_invalid("connection lost")
        assert not pool.is_valid
        assert pool.last_error is not None

    def test_mark_invalid_obfuscates_password(self, pool_factory):
        pool = pool_factory()
        pool.mark_invalid("failed to connect to postgresql://user:secret@host/db")
        assert "secret" not in (pool.last_error or "")
        assert "****" in (pool.last_error or "")


class TestEnsureConnected:
    @pytest.mark.asyncio
    async def test_returns_pool_when_valid(self, pool_factory):
        pool = pool_factory()
        mock_pool = AsyncMock()
        pool.pool = mock_pool
        pool._is_valid = True
        result = await pool.ensure_connected()
        assert result is mock_pool

    @pytest.mark.asyncio
    async def test_triggers_reconnect_when_invalid(self, pool_factory):
        pool = pool_factory(initial_delay=0.01)
        pool._is_valid = False
        mock_pool = _patch_create_pool(pool)

        async def fast_sleep(d):
            pass

        with patch("postgres_mcp.sql.sql_driver.asyncio.sleep", side_effect=fast_sleep):
            result = await pool.ensure_connected()
        assert result is mock_pool
        assert pool.reconnect_count == 1


class TestCrashProtection:
    @pytest.mark.asyncio
    async def test_reconnect_never_crashes_server(self, pool_factory):
        pool = pool_factory(initial_delay=0.01, max_attempts=1)
        pool._is_valid = False
        _patch_create_pool(pool, succeed=False)

        async def fast_sleep(d):
            pass

        with patch("postgres_mcp.sql.sql_driver.asyncio.sleep", side_effect=fast_sleep):
            try:
                await pool._reconnect_loop()
            except ConnectionError:
                pass
        assert pool.state == ConnState.ERROR
        assert pool.last_error is not None

    @pytest.mark.asyncio
    async def test_error_state_allows_retry(self, pool_factory):
        pool = pool_factory(initial_delay=0.01, max_attempts=1)
        pool._is_valid = False
        _patch_create_pool(pool, succeed=False)

        async def fast_sleep(d):
            pass

        with patch("postgres_mcp.sql.sql_driver.asyncio.sleep", side_effect=fast_sleep):
            with pytest.raises(ConnectionError):
                await pool._reconnect_loop()

        assert pool.state == ConnState.ERROR

        mock_pool = _patch_create_pool(pool, succeed=True)
        with patch("postgres_mcp.sql.sql_driver.asyncio.sleep", side_effect=fast_sleep):
            result = await pool._reconnect_loop()
        assert result is mock_pool
        assert pool.state == ConnState.CONNECTED


class TestEventCallback:
    @pytest.mark.asyncio
    async def test_events_emitted_on_connect(self):
        events: list[str] = []
        pool = DbConnPool(
            connection_url="postgresql://test:test@localhost/db",
            on_event=lambda msg: events.append(msg),
        )
        _patch_create_pool(pool)
        await pool.pool_connect()
        assert any("Connected" in e for e in events)

    @pytest.mark.asyncio
    async def test_events_emitted_on_reconnect(self):
        events: list[str] = []
        cfg = ReconnectConfig(initial_delay=0.01)
        pool = DbConnPool(
            connection_url="postgresql://test:test@localhost/db",
            reconnect_config=cfg,
            on_event=lambda msg: events.append(msg),
        )
        pool._is_valid = False
        _patch_create_pool(pool)

        async def fast_sleep(d):
            pass

        with patch("postgres_mcp.sql.sql_driver.asyncio.sleep", side_effect=fast_sleep):
            await pool._reconnect_loop()
        assert any("Reconnect attempt" in e for e in events)
        assert any("Reconnected" in e for e in events)

    @pytest.mark.asyncio
    async def test_events_emitted_on_mark_invalid(self):
        events: list[str] = []
        pool = DbConnPool(
            connection_url="postgresql://test:test@localhost/db",
            on_event=lambda msg: events.append(msg),
        )
        pool.mark_invalid("connection dropped")
        assert any("Connection lost" in e for e in events)
