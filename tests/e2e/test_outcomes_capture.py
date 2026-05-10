"""Capture real timing/recovery numbers for TESTING-METHODOLOGY.md.

This is not a regression test — it's a measurement harness. It runs
fault-injection scenarios, captures outcomes, and writes them to
/tmp/fluid-postgres-mcp-outcomes.json. The doc consumes the JSON.

Run only when refreshing the documented numbers:
    .venv/bin/pytest tests/e2e/test_outcomes_capture.py -v -s

Skipped by default to avoid running on every CI invocation.
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

import pytest

from mcp_client_fixtures import McpSession
from mcp_client_fixtures import call_tool
from mcp_client_fixtures import extract_text


OUTCOMES_PATH = Path("/tmp/fluid-postgres-mcp-outcomes.json")
WRONG_URL = "postgresql://nobody:wrong@127.0.0.1:1/nope"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
PASSTHROUGH = FIXTURES_DIR / "long_running_passthrough.sh"


pytestmark = pytest.mark.skipif(
    os.environ.get("CAPTURE_OUTCOMES") != "1",
    reason="set CAPTURE_OUTCOMES=1 to run measurement harness",
)


def _parse_status(text: str) -> dict:
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


def _event_messages(s: dict) -> list[str]:
    out = []
    for e in s.get("events") or []:
        if isinstance(e, dict):
            m = e.get("message")
            if isinstance(m, str):
                out.append(m)
        elif isinstance(e, str):
            out.append(e)
    return out


def _warning_messages(s: dict) -> list[str]:
    out = []
    for e in s.get("warnings") or []:
        if isinstance(e, dict):
            m = e.get("message")
            if isinstance(m, str):
                out.append(m)
        elif isinstance(e, str):
            out.append(e)
    return out


def _find_pid(s: dict) -> int | None:
    for m in _event_messages(s):
        if "Pre-connect-script started" in m and "pid=" in m:
            try:
                return int(m.split("pid=")[1].split(")")[0].strip())
            except Exception:
                continue
    return None


def _has_lost(s: dict) -> bool:
    for m in _event_messages(s):
        if "Connection lost" in m or "pre-connect-script exited" in m:
            return True
    return False


def _save(payload: dict) -> None:
    existing = {}
    if OUTCOMES_PATH.exists():
        try:
            existing = json.loads(OUTCOMES_PATH.read_text())
        except Exception:
            existing = {}
    existing.update(payload)
    OUTCOMES_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True))


@pytest.mark.asyncio
async def test_capture_bad_url_startup():
    extra = [
        "--hook-timeout", "2.0",
        "--reconnect-initial-delay", "0.5",
        "--reconnect-max-delay", "1.0",
        "--reconnect-max-attempts", "2",
    ]
    t0 = time.monotonic()
    async with McpSession(WRONG_URL, extra_args=extra) as session:
        startup_ms = round((time.monotonic() - t0) * 1000)
        status = await call_tool(session, "status", {"events": 20, "errors": 10})
        sj = _parse_status(extract_text(status))
        r = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
        _save({
            "bad_url_startup": {
                "startup_ms": startup_ms,
                "process_alive": True,
                "status_queryable": not status.isError,
                "state_reported": sj.get("state"),
                "query_returns_error_not_crash": r.isError,
            }
        })


@pytest.mark.asyncio
async def test_capture_malformed_db_url(tmp_path, k8s_pg_connection_string):
    real_url, _ = k8s_pg_connection_string
    bad = tmp_path / "malformed.sh"
    bad.write_text(dedent("""\
        #!/usr/bin/env bash
        set -euo pipefail
        printf '[MCP] DB_URL not-a-valid-url\\n'
        printf '[MCP] READY_TO_CONNECT\\n'
        exec sleep 2147483647
    """))
    bad.chmod(0o755)
    extra = ["--pre-connect-script", str(bad), "--hook-timeout", "10.0"]

    t0 = time.monotonic()
    async with McpSession(real_url, extra_args=extra) as session:
        r = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
        connect_ms = round((time.monotonic() - t0) * 1000)
        status = await call_tool(session, "status", {"events": 50, "warnings": 10})
        sj = _parse_status(extract_text(status))
        msgs = _event_messages(sj) + _warning_messages(sj)
        warning = any("DB_URL malformed" in m for m in msgs)
        _save({
            "malformed_db_url": {
                "connect_succeeded": not r.isError,
                "time_to_connect_ms": connect_ms,
                "warning_recorded": warning,
                "queries_lost": 0 if not r.isError else 1,
            }
        })


@pytest.mark.asyncio
async def test_capture_script_kill(k8s_pg_connection_string):
    real_url, _ = k8s_pg_connection_string
    extra = ["--pre-connect-script", str(PASSTHROUGH), "--hook-timeout", "10.0"]
    env = {"LONG_RUNNING_URL": real_url}

    runs = []
    for run_idx in range(3):
        async with McpSession(WRONG_URL, extra_args=extra, env_overrides=env) as session:
            r = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
            assert not r.isError

            status = await call_tool(session, "status", {"events": 50})
            pid = _find_pid(_parse_status(extract_text(status)))
            assert pid

            t0 = time.monotonic()
            os.kill(pid, signal.SIGTERM)

            detected_ms = None
            for _ in range(150):
                await asyncio.sleep(0.02)
                s = await call_tool(session, "status", {"events": 50})
                if _has_lost(_parse_status(extract_text(s))):
                    detected_ms = round((time.monotonic() - t0) * 1000)
                    break

            failed = 0
            recovered_ms = None
            for _ in range(50):
                r = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS r"})
                if not r.isError:
                    recovered_ms = round((time.monotonic() - t0) * 1000)
                    break
                failed += 1
                await asyncio.sleep(0.2)

            runs.append({
                "detection_ms": detected_ms,
                "recovery_ms": recovered_ms,
                "failed_queries": failed,
            })

    detections = [r["detection_ms"] for r in runs if r["detection_ms"] is not None]
    recoveries = [r["recovery_ms"] for r in runs if r["recovery_ms"] is not None]
    _save({
        "script_kill": {
            "runs": runs,
            "detection_ms_min": min(detections) if detections else None,
            "detection_ms_max": max(detections) if detections else None,
            "recovery_ms_min": min(recoveries) if recoveries else None,
            "recovery_ms_max": max(recoveries) if recoveries else None,
        }
    })


@pytest.mark.asyncio
async def test_capture_backend_terminate(k8s_pg_connection_string):
    """pg_terminate_backend then query — measure recovery."""
    import psycopg
    real_url, _ = k8s_pg_connection_string

    async with McpSession(real_url) as session:
        r = await call_tool(session, "execute_sql", {"sql": "SELECT pg_backend_pid() AS pid"})
        assert not r.isError
        # Open a side connection to kill our pool's backends.
        killer = await psycopg.AsyncConnection.connect(real_url, autocommit=True)
        try:
            rows = await killer.execute(
                "SELECT pid FROM pg_stat_activity "
                "WHERE pid != pg_backend_pid() AND application_name = ''"
            )
            pids = [row[0] for row in await rows.fetchall()]

            t0 = time.monotonic()
            for pid in pids:
                try:
                    await killer.execute(f"SELECT pg_terminate_backend({pid})")
                except Exception:
                    pass

            failed = 0
            recovered_ms = None
            for _ in range(30):
                r = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS r"})
                if not r.isError:
                    recovered_ms = round((time.monotonic() - t0) * 1000)
                    break
                failed += 1
                await asyncio.sleep(0.2)

            status = await call_tool(session, "status", {"metadata": True})
            sj = _parse_status(extract_text(status))
            meta = sj.get("metadata") or {}
            _save({
                "backend_terminate": {
                    "recovery_ms": recovered_ms,
                    "failed_queries": failed,
                    "reconnect_count_after": meta.get("reconnect_count"),
                }
            })
        finally:
            await killer.close()
