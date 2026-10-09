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


def _alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
    ).stdout
    return str(pid) in out


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


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
    grandchild_pid = int(child.stdout.readline().strip())
    return job, grandchild_pid


def test_terminate_ends_child_and_grandchild(tree):
    job, grandchild = _attach_and_read_grandchild(tree)
    job.terminate()
    job.close()
    assert _wait_dead(tree.pid)
    assert _wait_dead(grandchild)


def test_close_with_kill_on_close_ends_tree(tree):
    job, grandchild = _attach_and_read_grandchild(tree)
    job.close()
    assert _wait_dead(tree.pid)
    assert _wait_dead(grandchild)


def test_release_leaves_tree_running(tree):
    job, grandchild = _attach_and_read_grandchild(tree)
    job.release()
    time.sleep(0.5)
    assert _alive(tree.pid)
    assert _alive(grandchild)
    subprocess.run(["taskkill", "/F", "/PID", str(grandchild)], capture_output=True, check=False)
