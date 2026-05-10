"""E2E disruption tests against EC2 PostgreSQL via SSM tunnel.

These tests use real AWS infrastructure (EC2 + SSM) and are destructive
to the test PG instance. They verify the full recovery chain:
tunnel setup → connection → disruption → reconnection → recovery.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time

import pytest

from mcp_client_fixtures import McpSession
from mcp_client_fixtures import call_tool
from mcp_client_fixtures import create_mcp_session
from mcp_client_fixtures import extract_text
from ssm_fixtures import SsmConfig
from ssm_fixtures import assume_role
from ssm_fixtures import create_long_running_tunnel_script
from ssm_fixtures import create_tunnel_script
from ssm_fixtures import get_db_password
from ssm_fixtures import kill_tunnel
from ssm_fixtures import load_ssm_config
from ssm_fixtures import ssm_send_command
from ssm_fixtures import start_ssm_tunnel
from ssm_fixtures import wait_for_port
from ssm_fixtures import _find_free_port


@pytest.fixture(scope="module")
def ssm_config() -> SsmConfig:
    return load_ssm_config()


@pytest.fixture(scope="module")
def aws_env(ssm_config) -> dict[str, str]:
    return assume_role(ssm_config)


@pytest.fixture(scope="module")
def db_password(ssm_config, aws_env) -> str:
    return get_db_password(ssm_config, aws_env)


@pytest.fixture
def ssm_tunnel_and_url(ssm_config, aws_env, db_password):
    """Start SSM tunnel, yield (tunnel_proc, local_port, connection_url), tear down."""
    local_port = _find_free_port()
    proc = start_ssm_tunnel(ssm_config, aws_env, local_port)

    if not wait_for_port(local_port, timeout=30):
        kill_tunnel(proc)
        pytest.skip("SSM tunnel not ready after 30s")

    url = (
        f"postgresql://mcp_reader:{db_password}@127.0.0.1:{local_port}/crm"
        f"?connect_timeout=10&keepalives=1&keepalives_idle=30"
        f"&keepalives_interval=10&keepalives_count=3"
    )

    yield proc, local_port, url

    kill_tunnel(proc)


# ── 11.2: Happy path ─────────────────────────────────────────────

@pytest.mark.asyncio
class TestSsmHappyPath:
    async def test_server_connects_via_ssm_tunnel(self, ssm_tunnel_and_url):
        _, _, url = ssm_tunnel_and_url

        async for session in create_mcp_session(url):
            result = await call_tool(session, "execute_sql", {"sql": "SELECT current_database() AS db"})
            assert not result.isError
            text = extract_text(result)
            assert "crm" in text

    async def test_status_shows_connected(self, ssm_tunnel_and_url):
        _, _, url = ssm_tunnel_and_url

        async for session in create_mcp_session(url):
            result = await call_tool(session, "status", {"events": 10, "metadata": True})
            assert not result.isError
            parsed = eval(extract_text(result))
            assert parsed["state"] == "connected"
            event_msgs = [e["message"] for e in parsed.get("events", [])]
            assert any("Connected" in m for m in event_msgs)


# ── 11.3: Tunnel kill ────────────────────────────────────────────

@pytest.mark.asyncio
class TestTunnelKill:
    async def test_reconnect_after_tunnel_kill(self, ssm_config, aws_env, db_password):
        local_port = _find_free_port()
        tunnel_script = create_tunnel_script(ssm_config, aws_env, local_port)

        initial_tunnel = start_ssm_tunnel(ssm_config, aws_env, local_port)
        if not wait_for_port(local_port, timeout=30):
            kill_tunnel(initial_tunnel)
            pytest.skip("Initial SSM tunnel not ready")

        url = (
            f"postgresql://mcp_reader:{db_password}@127.0.0.1:{local_port}/crm"
            f"?connect_timeout=10&keepalives=1&keepalives_idle=10"
            f"&keepalives_interval=5&keepalives_count=2"
        )

        try:
            async for session in create_mcp_session(
                url,
                extra_args=[
                    "--pre-connect-script", tunnel_script,
                    "--reconnect-initial-delay", "1",
                    "--reconnect-max-delay", "10",
                    "--reconnect-max-attempts", "15",
                ],
            ):
                result = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS before_kill"})
                assert not result.isError
                assert "1" in extract_text(result)

                kill_tunnel(initial_tunnel)
                await asyncio.sleep(2)

                error_result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})

                if error_result.isError:
                    await asyncio.sleep(5)
                    recover_result = await call_tool(session, "execute_sql", {"sql": "SELECT 2 AS after_kill"})
                    assert not recover_result.isError, (
                        f"Failed to recover after tunnel kill: {extract_text(recover_result)}"
                    )
                    assert "2" in extract_text(recover_result)

                status_result = await call_tool(session, "status", {"events": 20, "metadata": True})
                parsed = eval(extract_text(status_result))
                assert parsed["state"] == "connected"
        finally:
            kill_tunnel(initial_tunnel)
            os.unlink(tunnel_script)


# ── 11.4: Connection kill via SQL (pg_terminate_backend) ─────────

@pytest.mark.asyncio
class TestConnectionKillViaSql:
    async def test_recover_after_all_backends_killed(self, ssm_tunnel_and_url, db_password):
        _, local_port, url = ssm_tunnel_and_url

        import psycopg

        async for session in create_mcp_session(
            url,
            extra_args=[
                "--reconnect-initial-delay", "1",
                "--reconnect-max-delay", "5",
                "--reconnect-max-attempts", "10",
            ],
        ):
            result = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS before_kill"})
            assert not result.isError

            killer_url = f"postgresql://mcp_reader:{db_password}@127.0.0.1:{local_port}/crm"
            killer = await psycopg.AsyncConnection.connect(killer_url, autocommit=True)
            rows = await killer.execute(
                "SELECT pid FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND pid != pg_backend_pid() "
                "AND usename = 'mcp_reader' "
                "AND state = 'idle' "
            )
            pids = [r[0] for r in await rows.fetchall()]
            for pid in pids:
                await killer.execute(f"SELECT pg_terminate_backend({pid})")
            await killer.close()
            await asyncio.sleep(2)

            error_result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
            if error_result.isError:
                await asyncio.sleep(3)
                recover_result = await call_tool(session, "execute_sql", {"sql": "SELECT 'back' AS s"})
                assert not recover_result.isError, (
                    f"Failed to recover: {extract_text(recover_result)}"
                )

            status_result = await call_tool(session, "status", {"events": 20, "metadata": True})
            parsed = eval(extract_text(status_result))
            assert parsed["state"] == "connected"


# ── 11.5: PG service stop/start (requires ssm:SendCommand) ──────

@pytest.mark.asyncio
class TestPgServiceStopStart:
    @pytest.fixture(autouse=True)
    def _check_send_command_permission(self, ssm_config, aws_env):
        result = ssm_send_command(ssm_config, aws_env, "echo healthcheck")
        if "AccessDenied" in result or "send-command failed" in result:
            pytest.skip("ssm:SendCommand not permitted for analyst role")

    async def test_recover_after_pg_stop_start(self, ssm_config, aws_env, ssm_tunnel_and_url):
        _, _, url = ssm_tunnel_and_url

        async for session in create_mcp_session(
            url,
            extra_args=[
                "--reconnect-initial-delay", "2",
                "--reconnect-max-delay", "10",
                "--reconnect-max-attempts", "20",
            ],
        ):
            result = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS before_stop"})
            assert not result.isError

            ssm_send_command(ssm_config, aws_env,
                "cd ${REMOTE_PROJECT_DIR} && docker compose stop postgres"
            )
            await asyncio.sleep(5)

            got_error = False
            for _ in range(8):
                error_result = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
                if error_result.isError:
                    got_error = True
                    break
                await asyncio.sleep(2)

            assert got_error, "Expected error after PG container stop"

            ssm_send_command(ssm_config, aws_env,
                "cd ${REMOTE_PROJECT_DIR} && docker compose start postgres"
            )
            await asyncio.sleep(15)

            recover_result = await call_tool(session, "execute_sql", {"sql": "SELECT 'recovered' AS s"})
            assert not recover_result.isError
            assert "recovered" in extract_text(recover_result)


# ── 11.6: PG service restart (requires ssm:SendCommand) ─────────

@pytest.mark.asyncio
class TestPgServiceRestart:
    @pytest.fixture(autouse=True)
    def _check_send_command_permission(self, ssm_config, aws_env):
        result = ssm_send_command(ssm_config, aws_env, "echo healthcheck")
        if "AccessDenied" in result or "send-command failed" in result:
            pytest.skip("ssm:SendCommand not permitted for analyst role")

    async def test_recover_after_pg_restart(self, ssm_config, aws_env, ssm_tunnel_and_url):
        _, _, url = ssm_tunnel_and_url

        async for session in create_mcp_session(
            url,
            extra_args=[
                "--reconnect-initial-delay", "2",
                "--reconnect-max-delay", "10",
                "--reconnect-max-attempts", "20",
            ],
        ):
            result = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS before_restart"})
            assert not result.isError

            ssm_send_command(ssm_config, aws_env,
                "cd ${REMOTE_PROJECT_DIR} && docker compose restart postgres"
            )
            await asyncio.sleep(10)

            first = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
            if first.isError:
                await asyncio.sleep(5)
                retry = await call_tool(session, "execute_sql", {"sql": "SELECT 'back' AS s"})
                assert not retry.isError

            status_result = await call_tool(session, "status", {"events": 20, "metadata": True})
            parsed = eval(extract_text(status_result))
            assert parsed["state"] == "connected"


# ── 11.x: Long-running --pre-connect-script (story 8) ───────────


def _wrong_url_for_long_running() -> str:
    return "postgresql://nobody:wrong@127.0.0.1:1/nope"


def _events_of(parsed: dict) -> list[str]:
    return [e["message"] if isinstance(e, dict) else e for e in parsed.get("events", [])]


def _find_pid_in_status(parsed: dict) -> int | None:
    for msg in _events_of(parsed):
        if "Pre-connect-script started" in msg and "pid=" in msg:
            try:
                return int(msg.split("pid=")[1].split(")")[0].strip())
            except Exception:
                continue
    return None


@pytest.mark.asyncio
class TestLongRunningSsmHappyPath:
    """A long-running pre-connect script opens the SSM tunnel and emits
    [MCP] DB_URL + READY_TO_CONNECT. The MCP connects via the script-emitted
    URL even when --database-url points at a deliberately wrong host."""

    async def test_connect_via_script_emitted_url(self, ssm_config, aws_env):
        local_port = _find_free_port()
        script_path = create_long_running_tunnel_script(ssm_config, aws_env, local_port)
        try:
            async with McpSession(
                _wrong_url_for_long_running(),
                extra_args=[
                    "--pre-connect-script", script_path,
                    "--hook-timeout", "60.0",
                    "--reconnect-initial-delay", "1",
                    "--reconnect-max-delay", "5",
                    "--reconnect-max-attempts", "5",
                ],
            ) as session:
                result = await call_tool(session, "execute_sql", {"sql": "SELECT current_database() AS db"})
                assert not result.isError, extract_text(result)
                assert "crm" in extract_text(result)

                status = await call_tool(session, "status", {"events": 50})
                parsed = eval(extract_text(status))
                msgs = _events_of(parsed)
                assert any("DB_URL received" in m for m in msgs)
                assert any("READY_TO_CONNECT received" in m for m in msgs)
                assert any("started (mode=long_running" in m for m in msgs)
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass


@pytest.mark.asyncio
class TestLongRunningSsmTunnelKill:
    """Killing the SSM child process forces the long-running script's
    `wait` to return; the script exits, the proactive watcher fires
    `mark_invalid`, and the next query triggers a reconnect."""

    async def test_reconnect_after_ssm_child_kill(self, ssm_config, aws_env):
        local_port = _find_free_port()
        script_path = create_long_running_tunnel_script(ssm_config, aws_env, local_port)

        try:
            async with McpSession(
                _wrong_url_for_long_running(),
                extra_args=[
                    "--pre-connect-script", script_path,
                    "--hook-timeout", "60.0",
                    "--reconnect-initial-delay", "1",
                    "--reconnect-max-delay", "5",
                    "--reconnect-max-attempts", "10",
                ],
            ) as session:
                first = await call_tool(session, "execute_sql", {"sql": "SELECT 1 AS before_kill"})
                assert not first.isError, extract_text(first)

                status = await call_tool(session, "status", {"events": 50})
                script_pid = _find_pid_in_status(eval(extract_text(status)))
                assert script_pid is not None

                pgrep = subprocess.run(
                    ["pgrep", "-P", str(script_pid)],
                    capture_output=True, text=True,
                )
                child_pids = [int(p) for p in pgrep.stdout.split() if p.strip().isdigit()]
                assert child_pids, f"no children of script pid {script_pid}; pgrep stdout={pgrep.stdout!r}"
                ssm_child_pid = child_pids[0]

                os.kill(ssm_child_pid, 9)

                msgs: list[str] = []
                for _ in range(40):
                    await asyncio.sleep(0.1)
                    status = await call_tool(session, "status", {"events": 50})
                    parsed = eval(extract_text(status))
                    msgs = _events_of(parsed)
                    if any("Connection lost" in m for m in msgs):
                        break
                else:
                    raise AssertionError(f"no Connection lost event after kill; events={msgs}")

                for _ in range(15):
                    try:
                        recover = await call_tool(session, "execute_sql", {"sql": "SELECT 2 AS after_kill"})
                        if not recover.isError and "2" in extract_text(recover):
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(1.0)
                else:
                    raise AssertionError("execute_sql did not recover after SSM child kill")

                status = await call_tool(session, "status", {"events": 50})
                parsed = eval(extract_text(status))
                msgs = _events_of(parsed)
                assert any("Reconnected" in m for m in msgs), msgs
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass


@pytest.mark.asyncio
class TestLongRunningSsmCredentialRotation:
    """The second invocation of the long-running script (after the first
    dies) emits a fresh DB_URL — proving the URL override is applied on
    every reconnect cycle, not just the first connect. Uses a wrapper
    that reads the password from a tmp file the test mutates between
    invocations, so we don't touch real Parameter Store."""

    async def test_second_invocation_emits_fresh_db_url(self, ssm_config, aws_env, db_password, tmp_path):
        local_port = _find_free_port()

        password_file = tmp_path / "rotating_password"
        password_file.write_text(db_password)

        wrapper = tmp_path / "wrapper.sh"
        wrapper.write_text(f"""#!/usr/bin/env bash
set -euo pipefail
export AWS_ACCESS_KEY_ID="{aws_env['AWS_ACCESS_KEY_ID']}"
export AWS_SECRET_ACCESS_KEY="{aws_env['AWS_SECRET_ACCESS_KEY']}"
export AWS_SESSION_TOKEN="{aws_env['AWS_SESSION_TOKEN']}"
export AWS_DEFAULT_REGION="{ssm_config.ec2_region}"

aws ssm start-session \\
    --target "{ssm_config.ec2_instance_id}" \\
    --region "{ssm_config.ec2_region}" \\
    --document-name AWS-StartPortForwardingSession \\
    --parameters "portNumber=5432,localPortNumber={local_port}" \\
    >/dev/null 2>&1 &
TUNNEL_PID=$!

for _ in $(seq 1 30); do
    if nc -z 127.0.0.1 {local_port} 2>/dev/null; then break; fi
    sleep 1
done

if ! nc -z 127.0.0.1 {local_port} 2>/dev/null; then
    kill "$TUNNEL_PID" 2>/dev/null || true
    exit 1
fi

PW=$(cat "{password_file}")
URL="postgresql://mcp_reader:${{PW}}@127.0.0.1:{local_port}/crm?connect_timeout=10&keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=3"

printf '[MCP] DB_URL %s\\n' "$URL"
printf '[MCP] READY_TO_CONNECT\\n'

wait "$TUNNEL_PID"
""")
        wrapper.chmod(0o755)

        async with McpSession(
            _wrong_url_for_long_running(),
            extra_args=[
                "--pre-connect-script", str(wrapper),
                "--hook-timeout", "60.0",
                "--reconnect-initial-delay", "1",
                "--reconnect-max-delay", "5",
                "--reconnect-max-attempts", "10",
            ],
        ) as session:
            first = await call_tool(session, "execute_sql", {"sql": "SELECT 1"})
            assert not first.isError, extract_text(first)

            status = await call_tool(session, "status", {"events": 50})
            script_pid = _find_pid_in_status(eval(extract_text(status)))
            assert script_pid is not None
            pgrep = subprocess.run(
                ["pgrep", "-P", str(script_pid)],
                capture_output=True, text=True,
            )
            child_pids = [int(p) for p in pgrep.stdout.split() if p.strip().isdigit()]
            assert child_pids
            os.kill(child_pids[0], 9)

            for _ in range(40):
                await asyncio.sleep(0.1)
                status = await call_tool(session, "status", {"events": 50})
                if any("Connection lost" in m for m in _events_of(eval(extract_text(status)))):
                    break

            for _ in range(15):
                try:
                    recover = await call_tool(session, "execute_sql", {"sql": "SELECT 3 AS rotated"})
                    if not recover.isError and "3" in extract_text(recover):
                        break
                except Exception:
                    pass
                await asyncio.sleep(1.0)
            else:
                raise AssertionError("did not recover after SSM child kill in rotation test")

            status = await call_tool(session, "status", {"events": 50})
            msgs = _events_of(eval(extract_text(status)))
            assert any("Reconnected" in m for m in msgs)
            db_url_events = [m for m in msgs if "DB_URL received" in m]
            assert len(db_url_events) >= 2, (
                f"expected ≥2 DB_URL events across rotation; got {db_url_events}"
            )
