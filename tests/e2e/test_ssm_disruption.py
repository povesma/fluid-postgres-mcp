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

from mcp_client_fixtures import call_tool
from mcp_client_fixtures import create_mcp_session
from mcp_client_fixtures import extract_text
from ssm_fixtures import SsmConfig
from ssm_fixtures import assume_role
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
