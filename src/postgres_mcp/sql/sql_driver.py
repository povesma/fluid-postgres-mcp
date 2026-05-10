"""SQL driver adapter for PostgreSQL connections."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from typing_extensions import LiteralString

from postgres_mcp.config import ReconnectConfig
from postgres_mcp.sql.connection_script import ConnectionScriptManager
from postgres_mcp.sql.connection_script import ScriptMode
from postgres_mcp.sql.utils import obfuscate_password

__all__ = ["ConnState", "DbConnPool", "SqlDriver", "obfuscate_password"]

logger = logging.getLogger(__name__)


class ConnState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    QUERYING = "querying"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class DbConnPool:
    """Database connection manager with automatic reconnection."""

    def __init__(
        self,
        connection_url: Optional[str] = None,
        reconnect_config: Optional[ReconnectConfig] = None,
        on_event: Optional[Callable[[str], None]] = None,
    ):
        self.connection_url = connection_url
        self.pool: AsyncConnectionPool | None = None
        self._is_valid = False
        self._last_error: Optional[str] = None
        self._state = ConnState.DISCONNECTED
        self._reconnect_count = 0
        self._reconnect_config = reconnect_config or ReconnectConfig()
        self._on_event = on_event
        self._script_mgr = ConnectionScriptManager(
            script=self._reconnect_config.pre_connect_script,
            hook_timeout=self._reconnect_config.hook_timeout,
            on_event=on_event,
        )
        self._exit_watcher_task: Optional[asyncio.Task] = None

    @property
    def state(self) -> ConnState:
        return self._state

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    @property
    def is_valid(self) -> bool:
        return self._is_valid

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def _emit(self, msg: str) -> None:
        if self._on_event:
            self._on_event(msg)

    async def _create_pool(self, url: str) -> AsyncConnectionPool:
        await self._close_pool_only()
        pool = AsyncConnectionPool(
            conninfo=url,
            min_size=1,
            max_size=5,
            open=False,
        )
        await pool.open()
        async with pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
        return pool

    async def pool_connect(self, connection_url: Optional[str] = None) -> AsyncConnectionPool:
        if self.pool and self._is_valid:
            return self.pool

        url = connection_url or self.connection_url
        self.connection_url = url
        if not url:
            self._is_valid = False
            self._last_error = "Database connection URL not provided"
            raise ValueError(self._last_error)

        outcome = await self._script_mgr.ensure_ready()
        if not outcome.success:
            self._state = ConnState.ERROR
            self._is_valid = False
            self._last_error = outcome.error or "Pre-connect script failed"
            raise ValueError(self._last_error)

        if outcome.db_url_override:
            url = outcome.db_url_override
            self.connection_url = url

        try:
            self.pool = await self._create_pool(url)
            self._is_valid = True
            self._last_error = None
            self._state = ConnState.CONNECTED
            self._emit("Connected to database")
            self._spawn_exit_watcher()
            return self.pool
        except Exception as e:
            self._is_valid = False
            self._last_error = str(e)
            self._state = ConnState.ERROR
            raise ValueError(f"Connection attempt failed: {obfuscate_password(str(e))}") from e

    async def _reconnect_loop(self) -> AsyncConnectionPool:
        self._state = ConnState.RECONNECTING
        cfg = self._reconnect_config
        url = self.connection_url
        if not url:
            self._state = ConnState.ERROR
            raise ValueError("No connection URL for reconnection")

        attempt = 0
        while True:
            attempt += 1
            max_att = cfg.max_attempts
            if max_att > 0 and attempt > max_att:
                self._state = ConnState.ERROR
                msg = f"Reconnection failed after {max_att} attempts"
                self._last_error = msg
                self._emit(msg)
                raise ConnectionError(msg)

            delay = min(cfg.initial_delay * (2 ** (attempt - 1)), cfg.max_delay)
            self._emit(f"Reconnect attempt {attempt} in {delay:.1f}s")
            logger.info("Reconnect attempt %d in %.1fs", attempt, delay)
            await asyncio.sleep(delay)

            outcome = await self._script_mgr.ensure_ready()
            if not outcome.success:
                self._last_error = outcome.error
                continue

            if outcome.db_url_override:
                url = outcome.db_url_override
                self.connection_url = url

            try:
                self.pool = await self._create_pool(url)
                self._is_valid = True
                self._last_error = None
                self._state = ConnState.CONNECTED
                self._reconnect_count += 1
                self._emit(f"Reconnected (attempt {attempt})")
                self._spawn_exit_watcher()
                return self.pool
            except Exception as e:
                self._last_error = obfuscate_password(str(e))
                logger.warning("Reconnect attempt %d failed: %s", attempt, self._last_error)

    async def ensure_connected(self) -> AsyncConnectionPool:
        if self.pool and self._is_valid:
            return self.pool
        return await self._reconnect_loop()

    def mark_invalid(self, error: str) -> None:
        self._is_valid = False
        self._last_error = obfuscate_password(error)
        self._emit(f"Connection lost: {self._last_error}")

    def _spawn_exit_watcher(self) -> None:
        """Spawn a task that invalidates the pool when a long-running script exits."""
        if self._script_mgr.mode is not ScriptMode.LONG_RUNNING:
            return
        if self._exit_watcher_task is not None and not self._exit_watcher_task.done():
            self._exit_watcher_task.cancel()
        self._exit_watcher_task = asyncio.create_task(self._watch_script_exit())

    async def _watch_script_exit(self) -> None:
        try:
            await self._script_mgr._exit_event.wait()
        except asyncio.CancelledError:
            raise
        if self._is_valid:
            self.mark_invalid("pre-connect-script exited")
            self._emit("Pre-connect-script restart requested")

    async def _close_pool_only(self) -> None:
        """Close just the psycopg pool. Leaves the script manager and
        watcher alive — used between reconnect attempts where the
        long-running script must keep owning the tunnel."""
        if self.pool:
            try:
                await self.pool.close()
            except Exception as e:
                logger.warning(f"Error closing connection pool: {e}")
            finally:
                self.pool = None
                self._is_valid = False

    async def close(self) -> None:
        if self._exit_watcher_task is not None and not self._exit_watcher_task.done():
            self._exit_watcher_task.cancel()
            try:
                await self._exit_watcher_task
            except (asyncio.CancelledError, Exception):
                pass
        self._exit_watcher_task = None
        try:
            await self._script_mgr.stop()
        except Exception as e:
            logger.warning("Error stopping pre-connect-script manager: %s", e)
        await self._close_pool_only()


class SqlDriver:
    """Adapter class that wraps a PostgreSQL connection with the interface expected by DTA."""

    @dataclass
    class RowResult:
        """Simple class to match the Griptape RowResult interface."""

        cells: Dict[str, Any]

    def __init__(
        self,
        conn: Any = None,
        engine_url: str | None = None,
        default_timeout_ms: int = 0,
    ):
        self.default_timeout_ms = default_timeout_ms
        if conn:
            self.conn = conn
            self.is_pool = isinstance(conn, DbConnPool)
        elif engine_url:
            self.engine_url = engine_url
            self.conn = None
            self.is_pool = False
        else:
            raise ValueError("Either conn or engine_url must be provided")

    def connect(self):
        if self.conn is not None:
            return self.conn
        if self.engine_url:
            self.conn = DbConnPool(self.engine_url)
            self.is_pool = True
            return self.conn
        else:
            raise ValueError("Connection not established. Either conn or engine_url must be provided")

    async def execute_query(
        self,
        query: LiteralString,
        params: list[Any] | None = None,
        force_readonly: bool = False,
        timeout_ms: Optional[int] = None,
    ) -> Optional[List[RowResult]]:
        effective_timeout = timeout_ms if timeout_ms is not None else self.default_timeout_ms
        try:
            if self.conn is None:
                self.connect()
                if self.conn is None:
                    raise ValueError("Connection not established")

            if self.is_pool:
                pool = await self.conn.ensure_connected()
                self.conn._state = ConnState.QUERYING
                try:
                    async with pool.connection() as connection:
                        result = await self._execute_with_connection(
                            connection, query, params,
                            force_readonly=force_readonly,
                            timeout_ms=effective_timeout,
                        )
                    self.conn._state = ConnState.CONNECTED
                    return result
                except Exception as e:
                    self.conn._state = ConnState.CONNECTED
                    self._handle_pool_error(e)
                    raise
            else:
                return await self._execute_with_connection(
                    self.conn, query, params,
                    force_readonly=force_readonly,
                    timeout_ms=effective_timeout,
                )
        except Exception as e:
            if not self.is_pool and self.conn:
                self.conn = None
            raise

    def _handle_pool_error(self, e: Exception) -> None:
        import psycopg
        if isinstance(e, (psycopg.OperationalError, OSError)):
            self.conn.mark_invalid(str(e))

    async def execute_to_file(
        self,
        query: str,
        file_path: str,
        timeout_ms: Optional[int] = None,
        on_progress: Optional[Callable] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        import csv
        import io
        import time
        from pathlib import Path

        effective_timeout = timeout_ms if timeout_ms is not None else self.default_timeout_ms

        path = Path(file_path)
        if not path.is_absolute() and output_dir:
            path = Path(output_dir) / path

        path.parent.mkdir(parents=True, exist_ok=True)

        if self.conn is None:
            self.connect()
            if self.conn is None:
                raise ValueError("Connection not established")

        if self.is_pool:
            pool = await self.conn.ensure_connected()
            self.conn._state = ConnState.QUERYING
            try:
                async with pool.connection() as connection:
                    result = await self._copy_to_file(
                        connection, query, str(path),
                        effective_timeout, on_progress,
                    )
                self.conn._state = ConnState.CONNECTED
                return result
            except Exception as e:
                self.conn._state = ConnState.CONNECTED
                self._handle_pool_error(e)
                raise
        else:
            return await self._copy_to_file(
                self.conn, query, str(path),
                effective_timeout, on_progress,
            )

    async def _copy_to_file(
        self, connection, query: str, file_path: str,
        timeout_ms: int, on_progress: Optional[Callable],
    ) -> Dict[str, Any]:
        import csv
        import io
        import time

        use_timeout = timeout_ms and timeout_ms > 0
        columns: list[str] = []
        total_bytes = 0
        approx_rows = 0
        start_time = time.monotonic()
        header_parsed = False
        progress_byte_threshold = 10 * 1024 * 1024
        progress_row_threshold = 100_000
        last_progress_bytes = 0
        last_progress_rows = 0

        copy_sql = f"COPY ({query}) TO STDOUT WITH CSV HEADER"

        async with connection.cursor() as cursor:
            if use_timeout:
                await cursor.execute("BEGIN")
                await cursor.execute(f"SET LOCAL statement_timeout = '{timeout_ms}'")

            with open(file_path, "wb") as f:
                async with cursor.copy(copy_sql) as copy:
                    async for raw in copy:
                        data = bytes(raw) if isinstance(raw, memoryview) else raw
                        if not header_parsed:
                            first_line_end = data.find(b"\n")
                            if first_line_end >= 0:
                                header_line = data[:first_line_end].decode("utf-8")
                                reader = csv.reader(io.StringIO(header_line))
                                columns = next(reader, [])
                                header_parsed = True

                        f.write(data)
                        total_bytes += len(data)
                        approx_rows += data.count(b"\n")

                        if on_progress:
                            bytes_since = total_bytes - last_progress_bytes
                            rows_since = approx_rows - last_progress_rows
                            if bytes_since >= progress_byte_threshold or rows_since >= progress_row_threshold:
                                elapsed = time.monotonic() - start_time
                                on_progress(approx_rows, total_bytes, elapsed)
                                last_progress_bytes = total_bytes
                                last_progress_rows = approx_rows

            row_count = 0
            if hasattr(cursor, "statusmessage") and cursor.statusmessage:
                parts = cursor.statusmessage.split()
                if len(parts) >= 2:
                    try:
                        row_count = int(parts[-1])
                    except ValueError:
                        row_count = max(0, approx_rows - 1)
            else:
                row_count = max(0, approx_rows - 1)

            if use_timeout:
                await cursor.execute("COMMIT")

        return {
            "file": file_path,
            "rows": row_count,
            "bytes": total_bytes,
            "columns": columns,
        }

    async def _execute_with_connection(
        self, connection, query, params, force_readonly, timeout_ms=0,
    ) -> Optional[List[RowResult]]:
        transaction_started = False
        use_timeout = timeout_ms and timeout_ms > 0
        try:
            async with connection.cursor(row_factory=dict_row) as cursor:
                if use_timeout or force_readonly:
                    if force_readonly:
                        await cursor.execute("BEGIN TRANSACTION READ ONLY")
                    else:
                        await cursor.execute("BEGIN")
                    transaction_started = True

                if use_timeout:
                    await cursor.execute(f"SET LOCAL statement_timeout = '{timeout_ms}'")

                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)

                while cursor.nextset():
                    pass

                if cursor.description is None:
                    if transaction_started:
                        await cursor.execute("COMMIT")
                        transaction_started = False
                    return None

                rows = await cursor.fetchall()

                if transaction_started:
                    await cursor.execute("COMMIT")
                    transaction_started = False

                return [SqlDriver.RowResult(cells=dict(row)) for row in rows]

        except Exception as e:
            if transaction_started:
                try:
                    await connection.rollback()
                except Exception as rollback_error:
                    logger.error(f"Error rolling back transaction: {rollback_error}")

            logger.error(f"Error executing query ({query}): {e}")
            raise e
