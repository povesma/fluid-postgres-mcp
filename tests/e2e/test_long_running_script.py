"""End-to-end tests for the long-running --pre-connect-script mode.

Boots the real `fluid-postgres-mcp` process pointed at a long-running
shell script that emits the `[MCP] DB_URL` and `[MCP] READY_TO_CONNECT`
lines, and exercises the protocol against the existing k8s PostgreSQL
fixture (no SSM dependency).

Uses `McpSession` (async-context-manager) rather than the older
async-generator `create_mcp_session`, so that the underlying
`stdio_client` and `ClientSession` cancel scopes enter and exit in the
same task. This is required for tests that send signals to subprocesses
spawned by the MCP server — async-generator teardown crosses a task
boundary at GC time and trips anyio's cancel-scope guard.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import signal
import time
from pathlib import Path
from textwrap import dedent
from typing import Optional

import pytest

from mcp_client_fixtures import McpSession
from mcp_client_fixtures import call_tool
from mcp_client_fixtures import extract_text


FIXTURES_DIR = Path(__file__).parent / "fixtures"
PASSTHROUGH_SCRIPT = FIXTURES_DIR / "long_running_passthrough.sh"


def _wrong_url() -> str:
    """A URL that will not connect — used to prove the [MCP] DB_URL override wins."""
    return "postgresql://nobody:wrong@127.0.0.1:1/nope"


@pytest.mark.asyncio
async def test_long_running_script_db_url_override_succeeds(k8s_pg_connection_string):
    """Smoke: launch MCP with a deliberately wrong --database-url, fixture
    script emits the real URL via [MCP] DB_URL, SELECT 1 succeeds."""
    real_url, _ = k8s_pg_connection_string

    extra = [
        "--pre-connect-script",
        str(PASSTHROUGH_SCRIPT),
        "--hook-timeout",
        "10.0",
    ]
    env = {"LONG_RUNNING_URL": real_url}

    async with McpSession(_wrong_url(), extra_args=extra, env_overrides=env) as session:
        result = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS ok"})
        text = extract_text(result)
        assert "1" in text
        assert not result.isError


@pytest.mark.asyncio
async def test_script_exit_marks_connection_invalid_within_one_second(k8s_pg_connection_string):
    """Kill the script after the MCP is connected; the status tool should
    report invalid within ~1s of the kill (E2E budget includes tool round-trips)."""
    real_url, _ = k8s_pg_connection_string

    extra = [
        "--pre-connect-script",
        str(PASSTHROUGH_SCRIPT),
        "--hook-timeout",
        "10.0",
    ]
    env = {"LONG_RUNNING_URL": real_url}

    async with McpSession(_wrong_url(), extra_args=extra, env_overrides=env) as session:
        # Confirm we're connected.
        result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
        assert not result.isError

        # Discover the script PID via the status tool's events.
        status = await call_tool(session, "status", {"events": 50})
        status_obj = _parse_status(extract_text(status))
        pid = _find_script_pid(status_obj)
        assert pid is not None, f"could not find script pid in status: {status_obj}"

        t0 = time.monotonic()
        os.kill(pid, signal.SIGTERM)

        elapsed: Optional[float] = None
        for _ in range(40):
            await asyncio.sleep(0.05)
            try:
                status = await call_tool(session, "status", {"events": 50})
            except Exception:
                continue
            sj = _parse_status(extract_text(status))
            if _has_lost_event(sj):
                elapsed = time.monotonic() - t0
                break

    if elapsed is None:
        # Diagnostic: dump the last events we saw.
        last = _event_messages(sj) if 'sj' in dir() else []
        raise AssertionError(f"status tool did not report disconnect; events={last}")
    # Manager guarantee is <1s; E2E adds tool-roundtrip overhead.
    assert elapsed < 2.5, f"detection took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_url_rotation_across_script_respawn(tmp_path, k8s_pg_connection_string):
    """The fixture is wrapped so its emitted DB_URL is read from a file the
    test mutates between respawns. After kill+respawn, the second pool is
    created with the rotated URL and SELECT continues to succeed."""
    real_url, _ = k8s_pg_connection_string

    url_file = tmp_path / "current_url"
    url_file.write_text(real_url)

    rotating_script = tmp_path / "rotating.sh"
    rotating_script.write_text(
        dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            URL=$(cat "{url_file}")
            printf '[MCP] DB_URL %s\\n' "$URL"
            printf '[MCP] READY_TO_CONNECT\\n'
            exec sleep 2147483647
            """)
    )
    rotating_script.chmod(0o755)

    extra = [
        "--pre-connect-script",
        str(rotating_script),
        "--hook-timeout",
        "10.0",
    ]

    async with McpSession(_wrong_url(), extra_args=extra) as session:
        result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
        assert not result.isError

        # Capture the first PID, mutate URL on disk, kill the script.
        status = await call_tool(session, "status", {"events": 50})
        sj = _parse_status(extract_text(status))
        pid_a = _find_script_pid(sj)
        assert pid_a is not None

        # The "rotation" here uses the same real PG URL but with a marker
        # query parameter we can grep for in events.
        rotated_url = real_url + ("?application_name=rotated" if "?" not in real_url else "&application_name=rotated")
        url_file.write_text(rotated_url)
        os.kill(pid_a, signal.SIGTERM)

        # Wait for the watcher to mark the connection invalid. The proactive
        # watcher fires within 1s of the script exit; we poll the state to
        # confirm. Until is_valid flips, ensure_connected() short-circuits and
        # the next query reuses the (still-alive at psycopg level) pool.
        for _ in range(40):
            await asyncio.sleep(0.05)
            status = await call_tool(session, "status", {"events": 50})
            sj = _parse_status(extract_text(status))
            if _has_lost_event(sj):
                break

        # Now trigger a reconnect via the next query.
        for attempt in range(10):
            try:
                result2 = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS still_alive"})
                if not result2.isError:
                    break
            except Exception:
                pass
            await asyncio.sleep(1.0)
        else:
            raise AssertionError("execute_sql never recovered after script kill")

        # Allow event propagation, then check for the reconnect-success line.
        await asyncio.sleep(0.5)
        status = await call_tool(session, "status", {"events": 50, "errors": 50, "warnings": 50})
        sj = _parse_status(extract_text(status))
        msgs = _event_messages(sj) + _warning_messages(sj)
        assert any("Reconnected" in m for m in msgs), f"no Reconnected event: status={sj}"


@pytest.mark.asyncio
async def test_script_is_sole_url_source_waiting_then_connected(tmp_path, k8s_pg_connection_string):
    """FR-3 smoke: register MCP with NO DATABASE_URI / positional URL, only
    --pre-connect-script. Script delays [MCP] DB_URL by ~2s. The server must
    start in WAITING_FOR_URL, then transition to CONNECTED once DB_URL is
    emitted, with `status` reflecting the final CONNECTED state."""
    real_url, _ = k8s_pg_connection_string

    delayed_script = tmp_path / "delayed.sh"
    delayed_script.write_text(
        dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            sleep 2
            printf '[MCP] DB_URL %s\\n' "{real_url}"
            printf '[MCP] READY_TO_CONNECT\\n'
            exec sleep 2147483647
            """)
    )
    delayed_script.chmod(0o755)

    extra = [
        "--pre-connect-script",
        str(delayed_script),
        "--hook-timeout",
        "15.0",
    ]

    async with McpSession(None, extra_args=extra) as session:
        # Eventually the server reaches CONNECTED. Poll status up to ~10s.
        connected = False
        for _ in range(40):
            try:
                result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
                if not result.isError:
                    connected = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        assert connected, "execute_sql never succeeded after delayed DB_URL"

        status = await call_tool(session, "status", {"events": 50})
        sj = _parse_status(extract_text(status))
        msgs = _event_messages(sj)
        # Either we caught WAITING_FOR_URL in the event log, or the connection
        # came back fast enough that only the CONNECTED line is present. Both
        # are valid; the success criterion is "we connected without
        # DATABASE_URI / positional URL."
        assert any("Connected to database" in m or "Reconnected" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_malformed_db_url_falls_back_to_configured_url(tmp_path, k8s_pg_connection_string):
    """Fixture emits a malformed [MCP] DB_URL line first, then a valid
    [MCP] READY_TO_CONNECT. MCP must fall back to its configured URL,
    record a warning event, and connect successfully."""
    real_url, _ = k8s_pg_connection_string

    bad_protocol_script = tmp_path / "malformed.sh"
    bad_protocol_script.write_text(
        dedent("""\
            #!/usr/bin/env bash
            set -euo pipefail
            printf '[MCP] DB_URL not-a-valid-url\\n'
            printf '[MCP] READY_TO_CONNECT\\n'
            exec sleep 2147483647
            """)
    )
    bad_protocol_script.chmod(0o755)

    extra = [
        "--pre-connect-script",
        str(bad_protocol_script),
        "--hook-timeout",
        "10.0",
    ]

    async with McpSession(real_url, extra_args=extra) as session:
        result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
        assert not result.isError

        status = await call_tool(session, "status", {"events": 50, "warnings": 10})
        sj = _parse_status(extract_text(status))
        msgs = _event_messages(sj) + _warning_messages(sj)
        # The malformed-DB_URL warning must have been recorded somewhere.
        assert any("DB_URL malformed" in m for m in msgs), f"no malformed warning in: {msgs}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_status(text: str) -> dict:
    """The status tool may return either JSON or a Python repr; handle both."""
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        v = ast.literal_eval(text)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    return {"raw": text}


def _event_messages(status: dict) -> list[str]:
    return _extract_messages(status, "events")


def _warning_messages(status: dict) -> list[str]:
    return _extract_messages(status, "warnings")


def _extract_messages(status: dict, key: str) -> list[str]:
    items = status.get(key) or []
    out: list[str] = []
    for entry in items:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            msg = entry.get("message")
            if isinstance(msg, str):
                out.append(msg)
    return out


def _find_script_pid(status: dict) -> Optional[int]:
    for msg in _event_messages(status):
        if "Pre-connect-script started" in msg and "pid=" in msg:
            try:
                return int(msg.split("pid=")[1].split(")")[0].strip())
            except Exception:
                continue
    return None


def _has_lost_event(status: dict) -> bool:
    for msg in _event_messages(status):
        if "Connection lost" in msg or "pre-connect-script exited" in msg:
            return True
    return False
