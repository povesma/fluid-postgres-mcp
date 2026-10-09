"""ThreadedProcess: subprocess.Popen with a stdout reader thread. Real processes, any OS."""

from __future__ import annotations

import asyncio
import sys

import pytest

from postgres_mcp.sql.threaded_process import ThreadedProcess


async def _read_all(proc: ThreadedProcess) -> list[bytes]:
    return [line async for line in proc.stdout]


async def _wait_returncode(proc: ThreadedProcess, timeout: float = 5.0) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while proc.returncode is None:
        assert loop.time() < deadline, "process did not exit in time"
        await asyncio.sleep(0.05)
    return proc.returncode


@pytest.mark.asyncio
async def test_lines_are_read_in_order_then_eof_and_exit_code():
    proc = ThreadedProcess.start([sys.executable, "-c", "print('one'); print('two'); raise SystemExit(3)"])
    lines = await asyncio.wait_for(_read_all(proc), timeout=5.0)
    assert [line.rstrip(b"\r\n") for line in lines] == [b"one", b"two"]
    assert await _wait_returncode(proc) == 3
    assert isinstance(proc.pid, int) and proc.pid > 0


@pytest.mark.asyncio
async def test_terminate_ends_a_sleeping_child():
    proc = ThreadedProcess.start([sys.executable, "-c", "import time; time.sleep(60)"])
    assert proc.returncode is None
    proc.terminate()
    assert await _wait_returncode(proc) is not None


@pytest.mark.asyncio
async def test_kill_ends_a_sleeping_child():
    proc = ThreadedProcess.start([sys.executable, "-c", "import time; time.sleep(60)"])
    proc.kill()
    assert await _wait_returncode(proc) is not None


@pytest.mark.asyncio
async def test_child_stdin_is_empty():
    proc = ThreadedProcess.start([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"])
    lines = await asyncio.wait_for(_read_all(proc), timeout=5.0)
    assert [line.rstrip(b"\r\n") for line in lines] == [b"''"]
    await _wait_returncode(proc)


@pytest.mark.asyncio
async def test_missing_executable_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        ThreadedProcess.start(["definitely-not-a-real-program-xyz"])
