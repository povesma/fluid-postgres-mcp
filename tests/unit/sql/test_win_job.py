"""WindowsJob against real processes. Runs only on Windows."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows job objects")

# Child waits for a line on stdin (sent after the job is attached, so the
# grandchild is created inside the job), starts a grandchild, prints its
# PID, then sleeps.
_CHILD = (
    "import subprocess, sys, time;"
    "sys.stdin.readline();"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']);"
    "print(g.pid, flush=True);"
    "time.sleep(600)"
)


class _Watch:
    """Handle to a running process, opened while it is known to be alive.

    Checks go through the handle, not the PID: Windows reuses PIDs quickly,
    and an open handle keeps this process object (and its PID) reserved.
    """

    _SYNCHRONIZE = 0x00100000
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _WAIT_OBJECT_0 = 0x0
    _WAIT_TIMEOUT = 0x102

    def __init__(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k = k
        self.pid = pid
        self._h = k.OpenProcess(self._SYNCHRONIZE | self._PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        assert self._h, f"cannot open process {pid} (already gone?)"

    def alive(self) -> bool:
        return self._k.WaitForSingleObject(self._h, 0) == self._WAIT_TIMEOUT

    def wait_dead(self, timeout: float = 5.0) -> bool:
        return self._k.WaitForSingleObject(self._h, int(timeout * 1000)) == self._WAIT_OBJECT_0

    def close(self) -> None:
        self._k.CloseHandle(self._h)


@pytest.fixture
def tree():
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    yield child
    for pid in (child.pid,):
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)


def _attach_and_read_grandchild(child):
    from postgres_mcp.sql.win_job import WindowsJob

    job = WindowsJob.for_pid(child.pid)
    child.stdin.write("go\n")
    child.stdin.flush()
    grandchild = _Watch(int(child.stdout.readline().strip()))
    return job, grandchild


def test_terminate_ends_child_and_grandchild(tree):
    job, grandchild = _attach_and_read_grandchild(tree)
    job.terminate()
    job.close()
    assert tree.wait(timeout=5) is not None
    assert grandchild.wait_dead()
    grandchild.close()


def test_close_with_kill_on_close_ends_tree(tree):
    job, grandchild = _attach_and_read_grandchild(tree)
    job.close()
    assert tree.wait(timeout=5) is not None
    assert grandchild.wait_dead()
    grandchild.close()


def test_manager_under_selector_loop_stops_script_and_grandchild(tmp_path):
    """Production conditions: postgres_mcp.main() installs the selector loop on Windows."""
    import asyncio

    from postgres_mcp.sql.connection_script import ConnectionScriptManager
    from postgres_mcp.sql.connection_script import ScriptMode

    grandchild_pid_file = tmp_path / "grandchild.pid"
    script = tmp_path / "tunnel.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
        f"open(r'{grandchild_pid_file}', 'w').write(str(g.pid))\n"
        "print('[MCP] READY_TO_CONNECT', flush=True)\n"
        "time.sleep(600)\n"
    )

    async def scenario():
        mgr = ConnectionScriptManager(script=f'"{sys.executable}" "{script}"', hook_timeout=30.0)
        outcome = await mgr.ensure_ready()
        # READY is printed after the PID file is written: both are alive here.
        watches = (_Watch(mgr._proc.pid), _Watch(int(grandchild_pid_file.read_text())))
        await mgr.stop()
        return outcome, watches

    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        outcome, (script, grandchild) = asyncio.run(scenario())
    finally:
        asyncio.set_event_loop_policy(previous)

    assert outcome.success is True
    assert outcome.mode is ScriptMode.LONG_RUNNING
    assert script.wait_dead()
    assert grandchild.wait_dead()
    script.close()
    grandchild.close()


def test_release_leaves_tree_running(tree):
    job, grandchild = _attach_and_read_grandchild(tree)
    job.release()
    time.sleep(0.5)
    assert tree.poll() is None
    assert grandchild.alive()
    subprocess.run(["taskkill", "/F", "/PID", str(grandchild.pid)], capture_output=True, check=False)
    grandchild.close()
