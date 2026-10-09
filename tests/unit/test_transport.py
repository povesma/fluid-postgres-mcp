import sys
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest


@pytest.fixture
def stub_pool(monkeypatch):
    from unittest.mock import MagicMock

    from postgres_mcp import server

    pool = MagicMock()
    pool.pool_connect = AsyncMock(return_value=None)
    pool.close = AsyncMock()
    monkeypatch.setattr(server, "DbConnPool", MagicMock(return_value=pool))
    return pool


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["stdio", "sse", "streamable-http"])
async def test_transport_argument_parsing(transport, stub_pool):
    """Test that all transport options are parsed correctly."""
    from postgres_mcp.server import main

    original_argv = sys.argv
    try:
        sys.argv = [
            "postgres_mcp",
            "postgresql://user:password@localhost/db",
            f"--transport={transport}",
        ]

        with (
            patch("postgres_mcp.server.mcp.run_stdio_async", AsyncMock()) as mock_stdio,
            patch("postgres_mcp.server.mcp.run_sse_async", AsyncMock()) as mock_sse,
            patch("postgres_mcp.server.mcp.run_streamable_http_async", AsyncMock()) as mock_http,
        ):
            await main()

            # Verify the correct transport method was called
            if transport == "stdio":
                mock_stdio.assert_called_once()
                mock_sse.assert_not_called()
                mock_http.assert_not_called()
            elif transport == "sse":
                mock_stdio.assert_not_called()
                mock_sse.assert_called_once()
                mock_http.assert_not_called()
            elif transport == "streamable-http":
                mock_stdio.assert_not_called()
                mock_sse.assert_not_called()
                mock_http.assert_called_once()
    finally:
        sys.argv = original_argv


@pytest.mark.asyncio
async def test_streamable_http_host_port_arguments(stub_pool):
    """Test that streamable-http host and port arguments are applied correctly."""
    from postgres_mcp.server import main
    from postgres_mcp.server import mcp

    original_argv = sys.argv
    try:
        sys.argv = [
            "postgres_mcp",
            "postgresql://user:password@localhost/db",
            "--transport=streamable-http",
            "--streamable-http-host=0.0.0.0",
            "--streamable-http-port=9000",
        ]

        with (
            patch("postgres_mcp.server.mcp.run_streamable_http_async", AsyncMock()),
        ):
            await main()

            # Verify the host and port were set correctly
            assert mcp.settings.host == "0.0.0.0"
            assert mcp.settings.port == 9000
    finally:
        sys.argv = original_argv


@pytest.mark.asyncio
async def test_sse_host_port_arguments(stub_pool):
    """Test that SSE host and port arguments are applied correctly."""
    from postgres_mcp.server import main
    from postgres_mcp.server import mcp

    original_argv = sys.argv
    try:
        sys.argv = [
            "postgres_mcp",
            "postgresql://user:password@localhost/db",
            "--transport=sse",
            "--sse-host=0.0.0.0",
            "--sse-port=8080",
        ]

        with (
            patch("postgres_mcp.server.mcp.run_sse_async", AsyncMock()),
        ):
            await main()

            # Verify the host and port were set correctly
            assert mcp.settings.host == "0.0.0.0"
            assert mcp.settings.port == 8080
    finally:
        sys.argv = original_argv


@pytest.mark.asyncio
async def test_default_transport_is_stdio(stub_pool):
    """Test that the default transport is stdio when not specified."""
    from postgres_mcp.server import main

    original_argv = sys.argv
    try:
        sys.argv = [
            "postgres_mcp",
            "postgresql://user:password@localhost/db",
        ]

        with (
            patch("postgres_mcp.server.mcp.run_stdio_async", AsyncMock()) as mock_stdio,
            patch("postgres_mcp.server.mcp.run_sse_async", AsyncMock()) as mock_sse,
            patch("postgres_mcp.server.mcp.run_streamable_http_async", AsyncMock()) as mock_http,
        ):
            await main()

            mock_stdio.assert_called_once()
            mock_sse.assert_not_called()
            mock_http.assert_not_called()
    finally:
        sys.argv = original_argv


# ---------------------------------------------------------------------------
# Exit path: main() is the single teardown owner
# ---------------------------------------------------------------------------


class _ExitHarness:
    """Runs server.main() with a stub pool, a stub stdio transport and
    captured signal handlers."""

    def __init__(self, monkeypatch, transport):
        import asyncio
        from unittest.mock import MagicMock

        from postgres_mcp import server

        self.server = server
        self.pool = MagicMock()
        self.pool.pool_connect = AsyncMock(return_value=None)
        self.pool.close = AsyncMock()
        self.handlers: dict = {}
        monkeypatch.setattr(server, "shutdown_in_progress", False)
        monkeypatch.setattr(server, "DbConnPool", MagicMock(return_value=self.pool))
        monkeypatch.setattr("sys.argv", ["fluid-postgres-mcp", "postgresql://u:p@localhost/db"])
        monkeypatch.setattr(server.mcp, "run_stdio_async", transport(self))

        def _add_signal_handler(sig, callback, *args):
            self.handlers[sig] = lambda: callback(*args)

        monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", _add_signal_handler)

    def fire(self, sig):
        self.handlers[sig]()


def _blocking_transport(fire_signals=()):
    """Transport that fires the given signals once running, then blocks."""
    import asyncio

    def factory(harness):
        async def run():
            for sig in fire_signals:
                asyncio.get_running_loop().call_soon(harness.fire, sig)
            await asyncio.Event().wait()

        return run

    return factory


@pytest.mark.asyncio
async def test_exit_normal_transport_return_closes_once(monkeypatch):
    def factory(harness):
        async def run():
            return None

        return run

    h = _ExitHarness(monkeypatch, factory)
    await h.server.main()
    assert h.pool.close.await_count == 1


@pytest.mark.asyncio
async def test_exit_transport_error_closes_once_and_propagates(monkeypatch):
    def factory(harness):
        async def run():
            raise RuntimeError("transport broke")

        return run

    h = _ExitHarness(monkeypatch, factory)
    with pytest.raises(RuntimeError, match="transport broke"):
        await h.server.main()
    assert h.pool.close.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGINT"])
async def test_exit_signal_closes_once_and_exits_128_plus_sig(monkeypatch, signame):
    import signal

    sig = getattr(signal, signame)
    h = _ExitHarness(monkeypatch, _blocking_transport([sig]))
    with pytest.raises(SystemExit) as ei:
        await h.server.main()
    assert ei.value.code == 128 + sig
    assert h.pool.close.await_count == 1


@pytest.mark.asyncio
async def test_exit_second_signal_does_not_close_twice(monkeypatch):
    import signal

    h = _ExitHarness(monkeypatch, _blocking_transport([signal.SIGTERM, signal.SIGINT]))
    with pytest.raises(SystemExit) as ei:
        await h.server.main()
    assert ei.value.code == 128 + signal.SIGTERM
    assert h.pool.close.await_count == 1


@pytest.mark.asyncio
async def test_exit_signal_during_teardown_does_not_interrupt_close(monkeypatch):
    """Stdin closed (transport returned), then SIGTERM arrives while close() runs."""
    import asyncio
    import signal

    def factory(harness):
        async def run():
            return None

        return run

    h = _ExitHarness(monkeypatch, factory)
    finished: list[bool] = []

    async def _close():
        h.fire(signal.SIGTERM)
        await asyncio.sleep(0.01)
        finished.append(True)

    h.pool.close.side_effect = _close
    with pytest.raises(SystemExit) as ei:
        await h.server.main()
    assert ei.value.code == 128 + signal.SIGTERM
    assert h.pool.close.await_count == 1
    assert finished == [True]


@pytest.mark.asyncio
async def test_exit_foreign_cancellation_closes_once_and_reraises(monkeypatch):
    import asyncio

    def factory(harness):
        async def run():
            asyncio.current_task().cancel()
            await asyncio.Event().wait()

        return run

    h = _ExitHarness(monkeypatch, factory)
    with pytest.raises(asyncio.CancelledError):
        await h.server.main()
    assert h.pool.close.await_count == 1


@pytest.mark.asyncio
async def test_exit_close_error_is_logged_not_raised(monkeypatch):
    def factory(harness):
        async def run():
            return None

        return run

    h = _ExitHarness(monkeypatch, factory)
    h.pool.close.side_effect = RuntimeError("close failed")
    await h.server.main()
    assert h.pool.close.await_count == 1
