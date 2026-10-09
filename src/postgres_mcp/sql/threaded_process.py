"""`subprocess.Popen` with a stdout reader thread.

Used on Windows, where `postgres_mcp.main()` installs the selector event
loop (needed by psycopg's async mode) and that loop cannot run asyncio
subprocesses. Offers the subset of `asyncio.subprocess.Process` that
`ConnectionScriptManager` uses: `pid`, `returncode`, `stdout` (async
iterator of byte lines), `terminate()`, `kill()`.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
from typing import Optional


class _LineStream:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

    def __aiter__(self) -> _LineStream:
        return self

    async def __anext__(self) -> bytes:
        line = await self._queue.get()
        if line is None:
            raise StopAsyncIteration
        return line


class ThreadedProcess:
    def __init__(self, popen: subprocess.Popen, loop: asyncio.AbstractEventLoop) -> None:
        self._popen = popen
        self.stdout = _LineStream()
        self._reader = threading.Thread(target=self._pump, args=(loop,), name=f"script-stdout-{popen.pid}", daemon=True)
        self._reader.start()

    @classmethod
    def start(cls, argv: list[str]) -> ThreadedProcess:
        """Start `argv`; must be called from the event loop. Raises OSError (e.g. FileNotFoundError)."""
        loop = asyncio.get_running_loop()
        # stdin=DEVNULL: on the stdio transport the MCP's own stdin carries the protocol.
        popen = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        return cls(popen, loop)

    @property
    def pid(self) -> int:
        return self._popen.pid

    @property
    def returncode(self) -> Optional[int]:
        return self._popen.poll()

    def terminate(self) -> None:
        self._popen.terminate()

    def kill(self) -> None:
        self._popen.kill()

    def _pump(self, loop: asyncio.AbstractEventLoop) -> None:
        stream = self._popen.stdout
        assert stream is not None
        try:
            for line in iter(stream.readline, b""):
                loop.call_soon_threadsafe(self.stdout._queue.put_nowait, line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                loop.call_soon_threadsafe(self.stdout._queue.put_nowait, None)
            except RuntimeError:
                pass  # event loop already closed
            stream.close()
