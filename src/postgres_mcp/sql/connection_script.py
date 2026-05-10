"""ConnectionScriptManager — long-running pre-connect-script support.

Owns the script subprocess, parses the `[MCP]` stdout protocol, races
mode detection (RUN_AND_EXIT vs LONG_RUNNING) on first `ensure_ready()`,
and exposes a small async API consumed by `DbConnPool`.

Intentionally knows nothing about psycopg or pool state.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable
from typing import Optional
from urllib.parse import urlparse

from postgres_mcp.sql.utils import obfuscate_password

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ScriptMode(str, Enum):
    NONE = "none"
    RUN_AND_EXIT = "run_and_exit"
    LONG_RUNNING = "long_running"


@dataclass
class ScriptOutcome:
    success: bool
    mode: ScriptMode
    db_url_override: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Protocol grammar
# ---------------------------------------------------------------------------


_PROTOCOL_RE = re.compile(r"^\[MCP\]\s+(?P<keyword>[A-Z_]+)(?:\s+(?P<payload>.*?))?\s*$")
_KEYWORD_READY = "READY_TO_CONNECT"
_KEYWORD_DB_URL = "DB_URL"


# ---------------------------------------------------------------------------
# ConnectionScriptManager
# ---------------------------------------------------------------------------


class ConnectionScriptManager:
    """Lifecycle owner for the `--pre-connect-script` subprocess.

    Modes are auto-detected on the first `ensure_ready()` call:
    * exit-before-READY → RUN_AND_EXIT
    * READY-before-exit → LONG_RUNNING
    * `hook_timeout` before either → kill + failure
    """

    def __init__(
        self,
        script: Optional[str],
        hook_timeout: float,
        on_event: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._script = script
        self._hook_timeout = hook_timeout
        self._on_event = on_event

        self._mode: ScriptMode = ScriptMode.NONE if script is None else ScriptMode.NONE
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._exit_watcher_task: Optional[asyncio.Task] = None

        self._ready_event = asyncio.Event()
        self._exit_event = asyncio.Event()
        self._db_url_override: Optional[str] = None
        self._lock = asyncio.Lock()

        self._unknown_keywords_logged: set[str] = set()
        self._inflight: Optional[asyncio.Future[ScriptOutcome]] = None
        self._exit_emitter_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def mode(self) -> ScriptMode:
        return self._mode

    async def wait_for_exit(self) -> int:
        if self._proc is None:
            raise RuntimeError("No script process to wait for")
        return await self._proc.wait()

    async def stop(self) -> None:
        await self._teardown()

    # ------------------------------------------------------------------

    async def ensure_ready(self) -> ScriptOutcome:
        if self._script is None:
            return ScriptOutcome(success=True, mode=ScriptMode.NONE)

        # If a detection is already in flight, share its result rather
        # than queueing a second READY-wait behind the lock.
        if self._inflight is not None:
            return await asyncio.shield(self._inflight)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ScriptOutcome] = loop.create_future()
        self._inflight = future
        try:
            async with self._lock:
                outcome = await self._ensure_ready_locked()
            future.set_result(outcome)
            return outcome
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            self._inflight = None

    async def _ensure_ready_locked(self) -> ScriptOutcome:
        assert self._script is not None

        if self._mode is ScriptMode.LONG_RUNNING and self.alive:
            return await self._await_next_ready()

        # Either no process yet, or previous process exited.
        try:
            await self._spawn()
        except _SpawnError as exc:
            return ScriptOutcome(
                success=False,
                mode=self._mode if self._mode is not ScriptMode.NONE else ScriptMode.RUN_AND_EXIT,
                db_url_override=self._db_url_override,
                error=str(exc),
            )
        return await self._await_first_ready()

    async def _await_next_ready(self) -> ScriptOutcome:
        """Long-running re-readiness: wait for the next READY or process exit."""
        ready_wait = asyncio.create_task(self._ready_event.wait())
        exit_wait = asyncio.create_task(self._exit_event.wait())
        try:
            done, _ = await asyncio.wait(
                {ready_wait, exit_wait},
                timeout=self._hook_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (ready_wait, exit_wait):
                if not t.done():
                    t.cancel()

        if not done:
            return ScriptOutcome(
                success=False,
                mode=ScriptMode.LONG_RUNNING,
                db_url_override=self._db_url_override,
                error=f"ready timeout after {self._hook_timeout}s",
            )

        if exit_wait in done and ready_wait not in done:
            await self._reap_reader()
            self._proc = None
            return ScriptOutcome(
                success=False,
                mode=ScriptMode.LONG_RUNNING,
                db_url_override=self._db_url_override,
                error="script exited",
            )

        self._emit("Pre-connect-script READY_TO_CONNECT received")
        self._ready_event.clear()
        return ScriptOutcome(
            success=True,
            mode=ScriptMode.LONG_RUNNING,
            db_url_override=self._db_url_override,
        )

    async def _spawn(self) -> None:
        assert self._script is not None
        self._ready_event.clear()
        self._exit_event.clear()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._script.split(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            self._proc = None
            raise _SpawnError(str(exc)) from exc

        # Reader task consumes stdout and dispatches protocol lines.
        self._reader_task = asyncio.create_task(self._reader_loop(self._proc))
        # Exit watcher signals _exit_event when the script dies.
        self._exit_watcher_task = asyncio.create_task(self._watch_exit(self._proc))

    async def _await_first_ready(self) -> ScriptOutcome:
        """Race exit vs READY vs hook_timeout for an as-yet-undetermined process."""
        proc = self._proc
        assert proc is not None

        ready_wait = asyncio.create_task(self._ready_event.wait())
        exit_wait = asyncio.create_task(self._exit_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {ready_wait, exit_wait},
                timeout=self._hook_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (ready_wait, exit_wait):
                if not t.done():
                    t.cancel()

        if not done:
            # Timeout: kill and tear down.
            self._emit(f"Pre-connect-script ready timeout after {self._hook_timeout}s")
            await self._teardown()
            return ScriptOutcome(
                success=False,
                mode=ScriptMode.RUN_AND_EXIT if self._mode is ScriptMode.NONE else self._mode,
                db_url_override=self._db_url_override,
                error=f"ready timeout after {self._hook_timeout}s",
            )

        if exit_wait in done and ready_wait not in done:
            # Process exited first → RUN_AND_EXIT.
            self._mode = ScriptMode.RUN_AND_EXIT
            code = proc.returncode if proc.returncode is not None else -1
            pid = proc.pid
            self._emit(f"Pre-connect-script started (mode=run_and_exit, pid={pid})")
            self._emit(f"Pre-connect-script exited (code={code})")
            await self._reap_reader()
            self._proc = None
            success = code == 0
            error = None if success else f"script exited with code {code}"
            return ScriptOutcome(
                success=success,
                mode=ScriptMode.RUN_AND_EXIT,
                db_url_override=self._db_url_override,
                error=error,
            )

        # READY arrived → LONG_RUNNING. Process and reader keep running.
        self._mode = ScriptMode.LONG_RUNNING
        self._emit(f"Pre-connect-script started (mode=long_running, pid={proc.pid})")
        self._emit("Pre-connect-script READY_TO_CONNECT received")
        self._ready_event.clear()
        # Spawn an event emitter that fires on eventual exit.
        if self._exit_emitter_task is None or self._exit_emitter_task.done():
            self._exit_emitter_task = asyncio.create_task(self._emit_on_exit())
        return ScriptOutcome(
            success=True,
            mode=ScriptMode.LONG_RUNNING,
            db_url_override=self._db_url_override,
        )

    async def _emit_on_exit(self) -> None:
        """Emit the 'exited' event when the long-running process dies."""
        try:
            await self._exit_event.wait()
        except asyncio.CancelledError:
            return
        proc = self._proc
        code = proc.returncode if proc is not None else -1
        self._emit(f"Pre-connect-script exited (code={code})")

    # ------------------------------------------------------------------
    # Subprocess plumbing
    # ------------------------------------------------------------------

    async def _reader_loop(self, proc: asyncio.subprocess.Process) -> None:
        try:
            stdout = proc.stdout
            if stdout is None:
                return
            async for raw in stdout:
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                except Exception:
                    continue
                self._handle_line(line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("script reader crashed: %s", obfuscate_password(str(exc)))

    async def _watch_exit(self, proc: asyncio.subprocess.Process) -> None:
        try:
            await proc.wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self._exit_event.set()

    async def _teardown(self) -> None:
        """Cancel reader/watcher tasks and kill any running process."""
        # Drain the exit emitter first so the 'exited' event fires
        # before we tear everything else down.
        if self._exit_emitter_task is not None and not self._exit_emitter_task.done():
            try:
                await asyncio.wait_for(self._exit_emitter_task, timeout=0.1)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        await self._reap_reader()
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
        self._proc = None

    async def _reap_reader(self) -> None:
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        self._reader_task = None
        if self._exit_watcher_task is not None and not self._exit_watcher_task.done():
            self._exit_watcher_task.cancel()
            try:
                await self._exit_watcher_task
            except (asyncio.CancelledError, Exception):
                pass
        self._exit_watcher_task = None

    def _handle_line(self, line: str) -> None:
        m = _PROTOCOL_RE.match(line)
        if m is None:
            # Diagnostic stdout — debug log only, password-obfuscated.
            logger.debug("[script] %s", obfuscate_password(line))
            return
        keyword = m.group("keyword")
        payload = m.group("payload") or ""
        if keyword == _KEYWORD_READY:
            self._ready_event.set()
        elif keyword == _KEYWORD_DB_URL:
            self._handle_db_url(payload)
        else:
            if keyword not in self._unknown_keywords_logged:
                self._unknown_keywords_logged.add(keyword)
                self._emit(f"Pre-connect-script unknown keyword: {keyword} (ignored)")

    def _handle_db_url(self, payload: str) -> None:
        if not payload:
            self._emit("Pre-connect-script DB_URL malformed: empty payload")
            return
        try:
            parsed = urlparse(payload)
        except Exception as exc:
            self._emit(f"Pre-connect-script DB_URL malformed: {exc}")
            return
        if not parsed.scheme or not parsed.netloc:
            self._emit("Pre-connect-script DB_URL malformed: missing scheme or netloc")
            return
        self._db_url_override = payload
        host = parsed.hostname or ""
        db = parsed.path.lstrip("/") or ""
        self._emit(f"Pre-connect-script DB_URL received (host={host}, db={db})")

    def _emit(self, msg: str) -> None:
        if self._on_event is not None:
            self._on_event(msg)


class _SpawnError(Exception):
    pass
