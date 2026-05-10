"""Unit tests for ConnectionScriptManager.

Uses a `FakeProcess` helper instead of mocking `asyncio.subprocess`
primitives directly; tests assert outward-visible behaviour
(`ScriptOutcome`, emitted events, `alive`, `wait_for_exit()`),
not internal task graph.
"""

from __future__ import annotations

import asyncio
from typing import List
from typing import Optional
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# FakeProcess — drop-in for asyncio.subprocess.Process
# ---------------------------------------------------------------------------


class _FakeStdout:
    """Async-iterable line stream backed by an asyncio.Queue.

    Yields bytes-with-trailing-newline (just like the real
    `process.stdout`). EOF is signalled by feeding `None`.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False

    def feed(self, line: str) -> None:
        if not line.endswith("\n"):
            line = line + "\n"
        self._queue.put_nowait(line.encode("utf-8"))

    def feed_eof(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class FakeProcess:
    """Fake `asyncio.subprocess.Process` for ConnectionScriptManager tests.

    Test code drives the process by calling `feed_line(...)`,
    `feed_eof()`, `set_exit_code(...)`. The manager-under-test sees
    the same surface as a real subprocess: `stdout`, `stderr`,
    `returncode`, `pid`, `wait()`, `kill()`, `terminate()`.
    """

    _next_pid = 10_000

    def __init__(self) -> None:
        FakeProcess._next_pid += 1
        self.pid: int = FakeProcess._next_pid
        self.stdout = _FakeStdout()
        self.stderr = _FakeStdout()
        self.returncode: Optional[int] = None
        self._exit_event = asyncio.Event()
        self.killed = False
        self.terminated = False

    # -- driven by tests --------------------------------------------------

    def feed_line(self, line: str) -> None:
        self.stdout.feed(line)

    def set_exit_code(self, code: int) -> None:
        self.returncode = code
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._exit_event.set()

    # -- consumed by manager-under-test -----------------------------------

    async def wait(self) -> int:
        await self._exit_event.wait()
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        if self.returncode is None:
            self.set_exit_code(-9)

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.set_exit_code(-15)

    async def communicate(self) -> tuple:
        # Drain stdout/stderr into bytes — used only by the
        # back-compat run-and-exit code path inside the manager.
        await self._exit_event.wait()
        return (b"", b"")


def install_fake_proc_factory(monkeypatch_or_patch_target: str):
    """Helper: install a fake `asyncio.create_subprocess_exec` that
    returns a fresh `FakeProcess` and records each spawn.

    Returns `(spawn_records, process_factory)` where:
      * `spawn_records` is a list appended to on every spawn:
        `(argv_tuple, fake_process)`.
      * `process_factory` lets the test customise the next FakeProcess
        before it is returned (call `process_factory.next = lambda fp: ...`).
    """

    spawn_records: List = []

    class _Factory:
        next: Optional[callable] = None

    factory = _Factory()

    async def fake_create_subprocess_exec(*argv, **kwargs):
        fp = FakeProcess()
        if factory.next is not None:
            factory.next(fp)
            factory.next = None
        spawn_records.append((argv, fp))
        return fp

    return spawn_records, factory, fake_create_subprocess_exec


# ---------------------------------------------------------------------------
# Smoke test — make sure the helper itself is sane before we start using it.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fakeprocess_yields_lines_then_eof():
    fp = FakeProcess()
    fp.feed_line("hello")
    fp.feed_line("world")
    fp.set_exit_code(0)

    lines: list[bytes] = []
    async for raw in fp.stdout:
        lines.append(raw)
    assert lines == [b"hello\n", b"world\n"]
    assert await fp.wait() == 0


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------


def _make_manager(script: Optional[str] = "/bin/true", hook_timeout: float = 0.5):
    from postgres_mcp.sql.connection_script import ConnectionScriptManager

    events: list[str] = []
    mgr = ConnectionScriptManager(
        script=script,
        hook_timeout=hook_timeout,
        on_event=events.append,
    )
    return mgr, events


# ---------------------------------------------------------------------------
# ScriptMode.NONE
# ---------------------------------------------------------------------------


class TestScriptModeNone:
    @pytest.mark.asyncio
    async def test_no_script_returns_immediate_success(self):
        from postgres_mcp.sql.connection_script import ScriptMode

        mgr, events = _make_manager(script=None)

        outcome = await mgr.ensure_ready()

        assert outcome.success is True
        assert outcome.mode is ScriptMode.NONE
        assert outcome.db_url_override is None
        assert outcome.error is None
        assert mgr.alive is False

    @pytest.mark.asyncio
    async def test_no_script_does_not_spawn_subprocess(self):
        spawns, _factory, fake_exec = install_fake_proc_factory("")
        mgr, _events = _make_manager(script=None)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()
        assert spawns == []


# ---------------------------------------------------------------------------
# RUN_AND_EXIT mode
# ---------------------------------------------------------------------------


class TestRunAndExitMode:
    @pytest.mark.asyncio
    async def test_exit_zero_before_ready_returns_success(self):
        from postgres_mcp.sql.connection_script import ScriptMode

        spawns, factory, fake_exec = install_fake_proc_factory("")
        factory.next = lambda fp: fp.set_exit_code(0)

        mgr, _events = _make_manager(script="/bin/true")
        with patch("asyncio.create_subprocess_exec", fake_exec):
            outcome = await mgr.ensure_ready()

        assert outcome.success is True
        assert outcome.mode is ScriptMode.RUN_AND_EXIT
        assert outcome.db_url_override is None
        assert outcome.error is None
        assert len(spawns) == 1

    @pytest.mark.asyncio
    async def test_exit_nonzero_before_ready_returns_failure(self):
        from postgres_mcp.sql.connection_script import ScriptMode

        _spawns, factory, fake_exec = install_fake_proc_factory("")
        factory.next = lambda fp: fp.set_exit_code(1)

        mgr, _events = _make_manager(script="/bin/false")
        with patch("asyncio.create_subprocess_exec", fake_exec):
            outcome = await mgr.ensure_ready()

        assert outcome.success is False
        assert outcome.mode is ScriptMode.RUN_AND_EXIT
        assert outcome.error is not None and "1" in outcome.error

    @pytest.mark.asyncio
    async def test_run_and_exit_argv_split_on_whitespace(self):
        spawns, factory, fake_exec = install_fake_proc_factory("")
        factory.next = lambda fp: fp.set_exit_code(0)

        mgr, _events = _make_manager(script="/usr/bin/env echo hi")
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()
        argv, _fp = spawns[0]
        assert argv == ("/usr/bin/env", "echo", "hi")

    @pytest.mark.asyncio
    async def test_argv_executable_name_only_passes_through(self):
        spawns, factory, fake_exec = install_fake_proc_factory("")
        factory.next = lambda fp: fp.set_exit_code(0)

        mgr, _events = _make_manager(script="my-tunnel-script")
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()
        argv, _fp = spawns[0]
        assert argv == ("my-tunnel-script",)


# ---------------------------------------------------------------------------
# LONG_RUNNING mode
# ---------------------------------------------------------------------------


class TestLongRunningMode:
    @pytest.mark.asyncio
    async def test_ready_before_exit_returns_long_running(self):
        from postgres_mcp.sql.connection_script import ScriptMode

        spawns, factory, fake_exec = install_fake_proc_factory("")
        factory.next = lambda fp: fp.feed_line("[MCP] READY_TO_CONNECT")

        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            outcome = await mgr.ensure_ready()

            assert outcome.success is True
            assert outcome.mode is ScriptMode.LONG_RUNNING
            assert outcome.db_url_override is None
            assert mgr.alive is True

            # Cleanup so the test process doesn't leak the FakeProcess.
            await mgr.stop()
            assert mgr.alive is False
        # Ensure we only spawned once.
        assert len(spawns) == 1

    @pytest.mark.asyncio
    async def test_db_url_then_ready_carries_override(self):
        spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            fp.feed_line("[MCP] DB_URL postgresql://u:p@h:1/db")
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            outcome = await mgr.ensure_ready()
            try:
                assert outcome.success is True
                assert outcome.db_url_override == "postgresql://u:p@h:1/db"
            finally:
                await mgr.stop()


# ---------------------------------------------------------------------------
# hook_timeout
# ---------------------------------------------------------------------------


class TestHookTimeout:
    @pytest.mark.asyncio
    async def test_no_output_no_exit_times_out_and_kills_process(self):
        _spawns, factory, fake_exec = install_fake_proc_factory("")
        # Capture the FakeProcess so we can assert it was killed.
        captured: list[FakeProcess] = []
        factory.next = lambda fp: captured.append(fp)

        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=0.05)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            outcome = await mgr.ensure_ready()

        assert outcome.success is False
        assert outcome.error is not None and "timeout" in outcome.error.lower()
        assert mgr.alive is False
        assert captured and captured[0].killed is True


# ---------------------------------------------------------------------------
# Protocol grammar
# ---------------------------------------------------------------------------


class TestProtocolGrammar:
    @pytest.mark.asyncio
    async def test_unknown_keyword_emits_warning_and_is_ignored(self):
        _spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            fp.feed_line("[MCP] WHATEVER some payload")
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            outcome = await mgr.ensure_ready()
            try:
                assert outcome.success is True
                # Warning event for unknown keyword present.
                assert any("unknown keyword" in e and "WHATEVER" in e for e in events)
            finally:
                await mgr.stop()

    @pytest.mark.asyncio
    async def test_unknown_keyword_warning_is_rate_limited_per_keyword(self):
        _spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            fp.feed_line("[MCP] FOO 1")
            fp.feed_line("[MCP] FOO 2")
            fp.feed_line("[MCP] FOO 3")
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            try:
                await mgr.ensure_ready()
            finally:
                await mgr.stop()
        assert sum(1 for e in events if "unknown keyword" in e and "FOO" in e) == 1

    @pytest.mark.asyncio
    async def test_malformed_db_url_emits_warning_and_keeps_prior_override(self):
        _spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            fp.feed_line("[MCP] DB_URL postgresql://u:p@h/db")
            fp.feed_line("[MCP] DB_URL not-a-url")
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            outcome = await mgr.ensure_ready()
            try:
                assert outcome.db_url_override == "postgresql://u:p@h/db"
                assert any("DB_URL malformed" in e for e in events)
            finally:
                await mgr.stop()

    @pytest.mark.asyncio
    async def test_non_protocol_lines_have_no_protocol_effect(self):
        from postgres_mcp.sql.connection_script import ScriptMode

        _spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            fp.feed_line("just some chatty stdout")
            fp.feed_line("  [MCP] READY_TO_CONNECT")  # leading whitespace breaks prefix
            fp.feed_line("[mcp] READY_TO_CONNECT")  # lowercase breaks prefix
            fp.feed_line("[MCP] READY_TO_CONNECT")  # this one wins

        factory.next = setup
        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            outcome = await mgr.ensure_ready()
            try:
                assert outcome.mode is ScriptMode.LONG_RUNNING
            finally:
                await mgr.stop()

    @pytest.mark.asyncio
    async def test_ready_keyword_with_trailing_payload_still_signals(self):
        _spawns, factory, fake_exec = install_fake_proc_factory("")

        # READY_TO_CONNECT spec says no payload, but we accept whitespace.
        def setup(fp):
            fp.feed_line("[MCP] READY_TO_CONNECT   ")

        factory.next = setup
        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            outcome = await mgr.ensure_ready()
            try:
                assert outcome.success is True
            finally:
                await mgr.stop()


# ---------------------------------------------------------------------------
# Re-readiness loop (long-running)
# ---------------------------------------------------------------------------


class TestLongRunningReReadiness:
    @pytest.mark.asyncio
    async def test_second_ensure_ready_awaits_next_ready_signal(self):
        from postgres_mcp.sql.connection_script import ScriptMode

        _spawns, factory, fake_exec = install_fake_proc_factory("")
        captured: list[FakeProcess] = []

        def setup(fp):
            captured.append(fp)
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            outcome1 = await mgr.ensure_ready()
            assert outcome1.mode is ScriptMode.LONG_RUNNING

            # Schedule a second READY a moment later; second ensure_ready
            # must await it.
            async def feed_second_ready():
                await asyncio.sleep(0.02)
                captured[0].feed_line("[MCP] READY_TO_CONNECT")

            feeder = asyncio.create_task(feed_second_ready())
            try:
                outcome2 = await mgr.ensure_ready()
                assert outcome2.success is True
                assert outcome2.mode is ScriptMode.LONG_RUNNING
            finally:
                await feeder
                await mgr.stop()

    @pytest.mark.asyncio
    async def test_second_ensure_ready_enforces_hook_timeout(self):
        _spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=0.05)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()
            try:
                outcome2 = await mgr.ensure_ready()
                assert outcome2.success is False
                assert outcome2.error and "timeout" in outcome2.error.lower()
            finally:
                await mgr.stop()


# ---------------------------------------------------------------------------
# Process-exit detection in LONG_RUNNING + re-spawn
# ---------------------------------------------------------------------------


class TestLongRunningExitDetection:
    @pytest.mark.asyncio
    async def test_wait_for_exit_resolves_when_script_dies(self):
        captured: list[FakeProcess] = []
        _spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            captured.append(fp)
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()
            assert mgr.alive is True

            captured[0].set_exit_code(0)
            code = await mgr.wait_for_exit()
            assert code == 0
            # alive turns false after process exits.
            assert mgr.alive is False
            await mgr.stop()

    @pytest.mark.asyncio
    async def test_next_ensure_ready_after_exit_respawns_script(self):
        spawns, factory, fake_exec = install_fake_proc_factory("")
        captured: list[FakeProcess] = []

        def setup(fp):
            captured.append(fp)
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup  # only the first spawn; we'll re-arm after.
        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()
            captured[0].set_exit_code(0)
            await mgr.wait_for_exit()

            # Re-arm: next spawn should produce a fresh process and emit READY.
            factory.next = setup
            outcome2 = await mgr.ensure_ready()
            try:
                assert outcome2.success is True
                assert len(spawns) == 2
                assert mgr.alive is True
            finally:
                await mgr.stop()


# ---------------------------------------------------------------------------
# Lock serialisation + stop()
# ---------------------------------------------------------------------------


class TestSerialisationAndStop:
    @pytest.mark.asyncio
    async def test_concurrent_ensure_ready_does_not_double_spawn(self):
        spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            o1, o2 = await asyncio.gather(mgr.ensure_ready(), mgr.ensure_ready())
            try:
                assert o1.success is True
                assert o2.success is True
                # Exactly one subprocess spawn.
                assert len(spawns) == 1
            finally:
                await mgr.stop()

    @pytest.mark.asyncio
    async def test_stop_kills_process_and_cancels_tasks(self):
        spawns, factory, fake_exec = install_fake_proc_factory("")
        captured: list[FakeProcess] = []

        def setup(fp):
            captured.append(fp)
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, _events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()
            assert mgr.alive is True

            await mgr.stop()
            assert mgr.alive is False
            # Process was killed (set_exit_code happened with -9).
            assert captured[0].killed is True or captured[0].returncode is not None


# ---------------------------------------------------------------------------
# DbConnPool integration: manager construction
# ---------------------------------------------------------------------------


class TestDbConnPoolConstructsManager:
    def test_pool_with_script_creates_manager(self):
        from postgres_mcp.config import ReconnectConfig
        from postgres_mcp.sql.connection_script import ConnectionScriptManager
        from postgres_mcp.sql.sql_driver import DbConnPool

        cfg = ReconnectConfig(pre_connect_script="/bin/true", hook_timeout=7.0)
        pool = DbConnPool(connection_url="postgresql://x:y@h/d", reconnect_config=cfg)
        assert isinstance(pool._script_mgr, ConnectionScriptManager)
        assert pool._script_mgr._script == "/bin/true"
        assert pool._script_mgr._hook_timeout == 7.0

    def test_pool_without_script_still_creates_manager_in_none_mode(self):
        from postgres_mcp.sql.connection_script import ConnectionScriptManager
        from postgres_mcp.sql.connection_script import ScriptMode
        from postgres_mcp.sql.sql_driver import DbConnPool

        pool = DbConnPool(connection_url="postgresql://x:y@h/d")
        assert isinstance(pool._script_mgr, ConnectionScriptManager)
        assert pool._script_mgr.mode is ScriptMode.NONE


# ---------------------------------------------------------------------------
# DbConnPool integration: pool_connect / _reconnect_loop delegation
# ---------------------------------------------------------------------------


class _StubManager:
    """Minimal ConnectionScriptManager test double."""

    def __init__(self, outcomes):
        from postgres_mcp.sql.connection_script import ScriptMode

        self._outcomes = list(outcomes)
        self.calls = 0
        self.stopped = False
        self._mode = ScriptMode.NONE
        # API surface mirrored from the real manager.
        self._exit_event = asyncio.Event()

    @property
    def mode(self):
        return self._mode

    @property
    def alive(self):
        return False

    async def ensure_ready(self):
        self.calls += 1
        if not self._outcomes:
            raise AssertionError("ensure_ready called more times than expected")
        return self._outcomes.pop(0)

    async def stop(self):
        self.stopped = True


def _outcome(success=True, override=None, error=None):
    from postgres_mcp.sql.connection_script import ScriptMode
    from postgres_mcp.sql.connection_script import ScriptOutcome

    return ScriptOutcome(
        success=success,
        mode=ScriptMode.LONG_RUNNING if success else ScriptMode.RUN_AND_EXIT,
        db_url_override=override,
        error=error,
    )


def _make_pool_with_stub(stub):
    from postgres_mcp.config import ReconnectConfig
    from postgres_mcp.sql.sql_driver import DbConnPool

    cfg = ReconnectConfig(pre_connect_script="/bin/true", hook_timeout=1.0, initial_delay=0.001)
    pool = DbConnPool(connection_url="postgresql://x:y@h/d", reconnect_config=cfg)
    pool._script_mgr = stub  # type: ignore[assignment]

    from unittest.mock import AsyncMock

    pool._create_pool = AsyncMock(return_value=AsyncMock())  # type: ignore[assignment]
    return pool


class TestDbConnPoolDelegatesToManager:
    @pytest.mark.asyncio
    async def test_pool_connect_calls_ensure_ready_once(self):
        stub = _StubManager([_outcome(success=True)])
        pool = _make_pool_with_stub(stub)
        await pool.pool_connect()
        assert stub.calls == 1

    @pytest.mark.asyncio
    async def test_pool_connect_failure_raises_value_error(self):
        from postgres_mcp.sql.sql_driver import ConnState

        stub = _StubManager([_outcome(success=False, error="boom")])
        pool = _make_pool_with_stub(stub)
        with pytest.raises(ValueError):
            await pool.pool_connect()
        assert pool._state is ConnState.ERROR
        assert pool.is_valid is False

    @pytest.mark.asyncio
    async def test_reconnect_loop_calls_ensure_ready_each_iteration(self):
        stub = _StubManager(
            [_outcome(success=False), _outcome(success=False), _outcome(success=True)]
        )
        pool = _make_pool_with_stub(stub)
        await pool._reconnect_loop()
        assert stub.calls == 3

    def test_run_pre_connect_hook_method_removed(self):
        from postgres_mcp.sql.sql_driver import DbConnPool

        assert not hasattr(DbConnPool, "_run_pre_connect_hook")


# ---------------------------------------------------------------------------
# DbConnPool integration: proactive disconnect watcher
# ---------------------------------------------------------------------------


class TestProactiveDisconnectWatcher:
    @pytest.mark.asyncio
    async def test_long_running_script_exit_marks_pool_invalid_within_one_second(self):
        from postgres_mcp.sql.connection_script import ScriptMode

        stub = _StubManager([_outcome(success=True)])
        stub._mode = ScriptMode.LONG_RUNNING
        pool = _make_pool_with_stub(stub)

        await pool.pool_connect()
        assert pool.is_valid is True

        # Fire the manager's exit signal — watcher should mark invalid quickly.
        import time

        t0 = time.monotonic()
        stub._exit_event.set()

        # Yield to the loop until the watcher runs.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if not pool.is_valid:
                break

        elapsed = time.monotonic() - t0
        assert pool.is_valid is False
        assert elapsed < 1.0, f"detection took {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_run_and_exit_does_not_spawn_watcher(self):
        from postgres_mcp.sql.connection_script import ScriptMode

        stub = _StubManager([_outcome(success=True)])
        stub._mode = ScriptMode.RUN_AND_EXIT
        pool = _make_pool_with_stub(stub)

        await pool.pool_connect()
        assert pool._exit_watcher_task is None

    @pytest.mark.asyncio
    async def test_close_stops_manager_and_cancels_watcher(self):
        from postgres_mcp.sql.connection_script import ScriptMode

        stub = _StubManager([_outcome(success=True)])
        stub._mode = ScriptMode.LONG_RUNNING
        pool = _make_pool_with_stub(stub)

        await pool.pool_connect()
        assert pool._exit_watcher_task is not None

        await pool.close()
        assert stub.stopped is True
        assert pool._exit_watcher_task is None or pool._exit_watcher_task.done()


# ---------------------------------------------------------------------------
# DB_URL override (story 4.0)
# ---------------------------------------------------------------------------


class TestDbUrlOverride:
    @pytest.mark.asyncio
    async def test_override_applied_to_initial_connect(self):
        new_url = "postgresql://mcp_reader:newpass@127.0.0.1:15432/crm"
        stub = _StubManager([_outcome(success=True, override=new_url)])
        pool = _make_pool_with_stub(stub)

        await pool.pool_connect()
        # _create_pool received the override.
        pool._create_pool.assert_called_once_with(new_url)
        assert pool.connection_url == new_url

    @pytest.mark.asyncio
    async def test_rotation_survives_across_disconnect(self):
        url_a = "postgresql://u:a@h/d"
        url_b = "postgresql://u:b@h/d"
        # First call: override A. Second call (after invalidation): override B.
        stub = _StubManager([_outcome(success=True, override=url_a),
                             _outcome(success=True, override=url_b)])
        pool = _make_pool_with_stub(stub)

        await pool.pool_connect()
        assert pool.connection_url == url_a

        # Invalidate and run reconnect loop once.
        pool.mark_invalid("simulated drop")
        await pool._reconnect_loop()

        assert pool.connection_url == url_b
        # Most recent _create_pool call was with url_b.
        last_args, _ = pool._create_pool.call_args
        assert last_args == (url_b,)

    @pytest.mark.asyncio
    async def test_no_override_uses_existing_url(self):
        # Outcome has db_url_override=None; pool keeps its configured URL.
        stub = _StubManager([_outcome(success=True, override=None)])
        pool = _make_pool_with_stub(stub)
        original_url = pool.connection_url

        await pool.pool_connect()
        assert pool.connection_url == original_url
        pool._create_pool.assert_called_once_with(original_url)

    @pytest.mark.asyncio
    async def test_malformed_db_url_does_not_crash_connect_path(self):
        # The manager itself emits a warning and keeps None as override,
        # so the pool sees override=None and proceeds with its URL.
        stub = _StubManager([_outcome(success=True, override=None)])
        pool = _make_pool_with_stub(stub)

        # Should not raise.
        await pool.pool_connect()
        assert pool.is_valid is True


# ---------------------------------------------------------------------------
# Event catalog (story 5.0)
# ---------------------------------------------------------------------------


class TestEventCatalog:
    @pytest.mark.asyncio
    async def test_run_and_exit_emits_started_and_exited(self):
        _spawns, factory, fake_exec = install_fake_proc_factory("")
        factory.next = lambda fp: fp.set_exit_code(0)

        mgr, events = _make_manager(script="/bin/true", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()

        joined = "\n".join(events)
        assert "Pre-connect-script started" in joined and "mode=run_and_exit" in joined
        assert "Pre-connect-script exited" in joined and "code=0" in joined

    @pytest.mark.asyncio
    async def test_long_running_emits_started_and_ready_and_exited(self):
        captured: list[FakeProcess] = []
        _spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            captured.append(fp)
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()
            captured[0].set_exit_code(0)
            await mgr.wait_for_exit()
            # Drive the watcher to fire one tick.
            await asyncio.sleep(0)
            await mgr.stop()

        joined = "\n".join(events)
        assert "mode=long_running" in joined
        assert "READY_TO_CONNECT received" in joined
        assert "Pre-connect-script exited" in joined

    @pytest.mark.asyncio
    async def test_db_url_event_redacts_password(self):
        _spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            fp.feed_line("[MCP] DB_URL postgresql://alice:supersecret@db.example/postgres")
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()
            await mgr.stop()

        joined = "\n".join(events)
        assert "DB_URL received" in joined
        assert "host=db.example" in joined
        assert "db=postgres" in joined or "db=/postgres" in joined
        assert "supersecret" not in joined

    @pytest.mark.asyncio
    async def test_ready_timeout_emits_event(self):
        _spawns, factory, fake_exec = install_fake_proc_factory("")
        factory.next = lambda fp: None  # never feed anything

        mgr, events = _make_manager(script="/bin/cat", hook_timeout=0.05)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()

        joined = "\n".join(events)
        assert "ready timeout" in joined.lower()

    @pytest.mark.asyncio
    async def test_no_password_substring_appears_in_any_event(self):
        password = "trustno1passw0rd"
        _spawns, factory, fake_exec = install_fake_proc_factory("")

        def setup(fp):
            fp.feed_line(f"[MCP] DB_URL postgresql://u:{password}@h:5432/dbn")
            fp.feed_line("[MCP] READY_TO_CONNECT")

        factory.next = setup
        mgr, events = _make_manager(script="/bin/cat", hook_timeout=2.0)
        with patch("asyncio.create_subprocess_exec", fake_exec):
            await mgr.ensure_ready()
            await mgr.stop()

        for e in events:
            assert password not in e, f"password leaked in event: {e!r}"

    @pytest.mark.asyncio
    async def test_pool_emits_restart_requested_on_script_exit(self):
        from postgres_mcp.sql.connection_script import ScriptMode

        events: list[str] = []
        stub = _StubManager([_outcome(success=True)])
        stub._mode = ScriptMode.LONG_RUNNING
        pool = _make_pool_with_stub(stub)
        pool._on_event = events.append

        await pool.pool_connect()
        events.clear()
        stub._exit_event.set()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if not pool.is_valid:
                break

        joined = "\n".join(events)
        assert "Connection lost" in joined
        assert "restart requested" in joined
