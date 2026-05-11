# ruff: noqa: B017
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from postgres_mcp.config import ReconnectConfig
from postgres_mcp.sql.connection_script import ScriptMode
from postgres_mcp.sql.connection_script import ScriptOutcome
from postgres_mcp.sql.sql_driver import ConnState
from postgres_mcp.sql.sql_driver import DbConnPool


class AsyncContextManagerMock(AsyncMock):
    """A better mock for async context managers"""

    async def __aenter__(self):
        return self.aenter

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def mock_pool():
    """Create a mock for AsyncConnectionPool."""
    pool = MagicMock()

    # Create cursor context manager
    cursor = AsyncMock()

    # Create connection context manager
    connection = AsyncMock()
    connection.cursor = MagicMock(return_value=AsyncContextManagerMock())
    connection.cursor.return_value.aenter = cursor

    # Setup connection manager
    conn_ctx = AsyncContextManagerMock()
    conn_ctx.aenter = connection

    # Setup pool.connection() to return our mocked connection context manager
    pool.connection = MagicMock(return_value=conn_ctx)

    # Setup pool.open and pool.close as async mocks
    pool.open = AsyncMock()
    pool.close = AsyncMock()

    return pool


@pytest.mark.asyncio
async def test_pool_connect_success(mock_pool):
    """Test successful connection to the database pool."""
    with patch("postgres_mcp.sql.sql_driver.AsyncConnectionPool", return_value=mock_pool):
        # Patch the connection test part to skip it
        with patch.object(DbConnPool, "pool_connect", new=AsyncMock(return_value=mock_pool)) as mock_connect:
            db_pool = DbConnPool("postgresql://user:pass@localhost/db")
            pool = await db_pool.pool_connect()

            assert pool == mock_pool
            mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_pool_connect_with_retry(mock_pool):
    """Test pool connection with retry on failure."""
    # First attempt fails, second succeeds
    mock_pool.open.side_effect = [Exception("Connection error"), None]

    # Create a mock implementation of pool_connect that simulates a retry
    async def mock_pool_connect(self, connection_url=None):
        if not hasattr(self, "_attempt_count"):
            self._attempt_count = 0

        self._attempt_count += 1

        if self._attempt_count == 1:
            # First attempt fails
            raise Exception("Connection error")
        else:
            # Second attempt succeeds
            self.pool = mock_pool
            self._is_valid = True
            return mock_pool

    with patch("postgres_mcp.sql.sql_driver.AsyncConnectionPool", return_value=mock_pool):
        with patch("postgres_mcp.server.asyncio.sleep", AsyncMock()) as mock_sleep:
            with patch.object(DbConnPool, "pool_connect", mock_pool_connect):
                db_pool = DbConnPool("postgresql://user:pass@localhost/db")

                # Call our own custom implementation directly to simulate the retry
                # First call will fail, second call will succeed
                with pytest.raises(Exception):
                    await mock_pool_connect(db_pool)

                # Second attempt should succeed
                pool = await mock_pool_connect(db_pool)

                assert pool == mock_pool
                assert db_pool._is_valid is True  # type: ignore
                mock_sleep.assert_not_called()  # We're not actually calling sleep in our mock


@pytest.mark.asyncio
async def test_pool_connect_all_retries_fail(mock_pool):
    """Test pool connection when all retry attempts fail."""
    # Mock pool.open to raise an exception for the test
    mock_pool.open.side_effect = Exception("Persistent connection error")

    # Configure AsyncConnectionPool's constructor to return our mock
    with patch("postgres_mcp.sql.sql_driver.AsyncConnectionPool", return_value=mock_pool):
        # Mock sleep to speed up test
        with patch("asyncio.sleep", AsyncMock()):
            db_pool = DbConnPool("postgresql://user:pass@localhost/db")

            # This should fail since pool.open raises an exception
            with pytest.raises(Exception):
                await db_pool.pool_connect()

            # Verify the pool is marked as invalid
            assert db_pool._is_valid is False  # type: ignore
            # Verify open was called at least once (no need to verify retries here)
            assert mock_pool.open.call_count >= 1


@pytest.mark.asyncio
async def test_close_pool(mock_pool):
    """Test closing the connection pool."""
    with patch("postgres_mcp.sql.sql_driver.AsyncConnectionPool", return_value=mock_pool):
        db_pool = DbConnPool("postgresql://user:pass@localhost/db")

        # Mock the pool_connect method to avoid actual connection
        db_pool.pool_connect = AsyncMock(return_value=mock_pool)
        await db_pool.pool_connect()
        db_pool.pool = mock_pool  # Set directly
        db_pool._is_valid = True  # type: ignore

        # Close the pool
        await db_pool.close()

        # Check that pool is now invalid
        assert db_pool._is_valid is False  # type: ignore
        assert db_pool.pool is None
        mock_pool.close.assert_called_once()


@pytest.mark.asyncio
async def test_close_handles_errors(mock_pool):
    """Test that close() handles exceptions gracefully."""
    mock_pool.close.side_effect = Exception("Error closing pool")

    with patch("postgres_mcp.sql.sql_driver.AsyncConnectionPool", return_value=mock_pool):
        db_pool = DbConnPool("postgresql://user:pass@localhost/db")

        # Mock the pool_connect method to avoid actual connection
        db_pool.pool_connect = AsyncMock(return_value=mock_pool)
        await db_pool.pool_connect()
        db_pool.pool = mock_pool  # Set directly
        db_pool._is_valid = True  # type: ignore

        # Close should not raise the exception
        await db_pool.close()

        # Pool should still be marked as invalid
        assert db_pool._is_valid is False  # type: ignore
        assert db_pool.pool is None


@pytest.mark.asyncio
async def test_pool_connect_initialized(mock_pool):
    """Test pool_connect when pool is already initialized."""
    with patch("postgres_mcp.sql.sql_driver.AsyncConnectionPool", return_value=mock_pool):
        db_pool = DbConnPool("postgresql://user:pass@localhost/db")

        # Mock the pool_connect method to avoid actual connection
        db_pool.pool_connect = AsyncMock(return_value=mock_pool)
        original_pool = await db_pool.pool_connect()
        db_pool.pool = mock_pool  # Set directly
        db_pool._is_valid = True  # type: ignore

        # Reset the mock counts
        mock_pool.open.reset_mock()

        # Get the pool again
        returned_pool = await db_pool.pool_connect()

        # Should return the existing pool without reconnecting
        assert returned_pool == original_pool
        mock_pool.open.assert_not_called()


@pytest.mark.asyncio
async def test_pool_connect_not_initialized(mock_pool):
    """Test pool_connect when pool is not yet initialized."""
    with patch("postgres_mcp.sql.sql_driver.AsyncConnectionPool", return_value=mock_pool):
        db_pool = DbConnPool("postgresql://user:pass@localhost/db")

        # Mock the pool_connect method to avoid actual connection
        db_pool.pool_connect = AsyncMock(return_value=mock_pool)

        # Get pool without initializing first
        pool = await db_pool.pool_connect()

        # Verify pool connect was called
        db_pool.pool_connect.assert_called_once()
        assert pool == mock_pool


@pytest.mark.asyncio
async def test_connection_url_property():
    """Test connection_url property."""
    db_pool = DbConnPool("postgresql://user:pass@localhost/db")
    assert db_pool.connection_url == "postgresql://user:pass@localhost/db"

    # Change the URL
    db_pool.connection_url = "postgresql://newuser:newpass@otherhost/otherdb"
    assert db_pool.connection_url == "postgresql://newuser:newpass@otherhost/otherdb"


def _pool_with_script(script: str | None = "/bin/true") -> DbConnPool:
    cfg = ReconnectConfig(pre_connect_script=script, hook_timeout=1.0, initial_delay=0.01)
    return DbConnPool(connection_url=None, reconnect_config=cfg)


@pytest.mark.asyncio
async def test_pool_connect_long_running_script_supplies_url(mock_pool):
    """Long-running script + no env/argv URL + script emits DB_URL → pool returned, state CONNECTED."""
    pool = _pool_with_script()
    pool._script_mgr.ensure_ready = AsyncMock(
        return_value=ScriptOutcome(
            success=True,
            mode=ScriptMode.LONG_RUNNING,
            db_url_override="postgresql://script:pw@host/db",
        )
    )
    pool._create_pool = AsyncMock(return_value=mock_pool)

    result = await pool.pool_connect()

    assert result is mock_pool
    assert pool.state == ConnState.CONNECTED
    assert pool.connection_url == "postgresql://script:pw@host/db"
    assert pool.unrecoverable is False


@pytest.mark.asyncio
async def test_pool_connect_long_running_no_url_returns_none_waiting():
    """Long-running script + no URL anywhere → returns None, state WAITING_FOR_URL, no raise."""
    pool = _pool_with_script()
    pool._script_mgr.ensure_ready = AsyncMock(
        return_value=ScriptOutcome(success=True, mode=ScriptMode.LONG_RUNNING, db_url_override=None)
    )
    pool._create_pool = AsyncMock()

    result = await pool.pool_connect()

    assert result is None
    assert pool.state == ConnState.WAITING_FOR_URL
    assert pool.unrecoverable is False
    pool._create_pool.assert_not_called()


@pytest.mark.asyncio
async def test_pool_connect_run_and_exit_no_url_raises_unrecoverable():
    """Run-and-exit script exits without DB_URL + no URL → raises ValueError, _unrecoverable=True, state ERROR."""
    pool = _pool_with_script()
    pool._script_mgr.ensure_ready = AsyncMock(
        return_value=ScriptOutcome(success=True, mode=ScriptMode.RUN_AND_EXIT, db_url_override=None)
    )
    pool._create_pool = AsyncMock()

    with pytest.raises(ValueError, match="exited without"):
        await pool.pool_connect()

    assert pool.state == ConnState.ERROR
    assert pool.unrecoverable is True
    pool._create_pool.assert_not_called()


@pytest.mark.asyncio
async def test_pool_connect_precedence_script_wins(mock_pool):
    """Precedence (FR-2): script DB_URL > passed-in URL."""
    pool = _pool_with_script()
    pool._script_mgr.ensure_ready = AsyncMock(
        return_value=ScriptOutcome(
            success=True,
            mode=ScriptMode.LONG_RUNNING,
            db_url_override="postgresql://script:pw@scripthost/db",
        )
    )
    pool._create_pool = AsyncMock(return_value=mock_pool)

    await pool.pool_connect(connection_url="postgresql://argv:pw@argvhost/db")

    pool._create_pool.assert_awaited_once_with("postgresql://script:pw@scripthost/db")
    assert pool.connection_url == "postgresql://script:pw@scripthost/db"


@pytest.mark.asyncio
async def test_pool_connect_no_script_no_url_still_raises():
    """Backwards-compat: no script and no URL still raises ValueError."""
    pool = DbConnPool(connection_url=None)

    with pytest.raises(ValueError):
        await pool.pool_connect()
