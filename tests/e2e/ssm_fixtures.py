"""SSM tunnel + EC2 disruption helpers for E2E tests.

Uses the same AWS auth flow as mcp-crm.sh: load .env.system,
export credentials from AWS_ANALYST_PROFILE, assume ANALYST_ROLE_ARN.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

import pytest

logger = logging.getLogger(__name__)

ENV_SYSTEM_PATH = os.path.expanduser("${SSM_ENV_FILE}")


@dataclass
class SsmConfig:
    ec2_instance_id: str
    ec2_region: str
    analyst_role_arn: str
    aws_analyst_profile: str


def load_ssm_config() -> SsmConfig:
    if not os.path.exists(ENV_SYSTEM_PATH):
        pytest.skip(f".env.system not found at {ENV_SYSTEM_PATH}")

    env = {}
    with open(ENV_SYSTEM_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip()

    required = ["EC2_INSTANCE_ID", "EC2_REGION", "ANALYST_ROLE_ARN", "AWS_ANALYST_PROFILE"]
    for key in required:
        if key not in env:
            pytest.skip(f"Missing {key} in .env.system")

    return SsmConfig(
        ec2_instance_id=env["EC2_INSTANCE_ID"],
        ec2_region=env["EC2_REGION"],
        analyst_role_arn=env["ANALYST_ROLE_ARN"],
        aws_analyst_profile=env["AWS_ANALYST_PROFILE"],
    )


def _run(cmd: list[str], env: dict | None = None, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if check and result.returncode != 0:
        logger.error("Command failed: %s\nstderr: %s", " ".join(cmd), result.stderr[:500])
    return result


def assume_role(config: SsmConfig) -> dict[str, str]:
    export_result = _run([
        "aws", "configure", "export-credentials",
        "--profile", config.aws_analyst_profile,
        "--format", "env-no-export",
    ], check=False)

    if export_result.returncode != 0:
        pytest.skip(f"AWS profile {config.aws_analyst_profile} not configured or expired")

    base_env = {**os.environ}
    for line in export_result.stdout.strip().split("\n"):
        if "=" in line:
            key, val = line.split("=", 1)
            base_env[key.strip()] = val.strip()

    assume_result = _run([
        "aws", "sts", "assume-role",
        "--role-arn", config.analyst_role_arn,
        "--role-session-name", f"e2e-test-{int(time.time())}",
        "--duration-seconds", "3600",
        "--region", config.ec2_region,
        "--output", "json",
    ], env=base_env, check=False)

    if assume_result.returncode != 0:
        pytest.skip(f"Failed to assume role: {assume_result.stderr[:200]}")

    import json
    creds = json.loads(assume_result.stdout)["Credentials"]

    aws_env = {
        **os.environ,
        "AWS_ACCESS_KEY_ID": creds["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
        "AWS_SESSION_TOKEN": creds["SessionToken"],
        "AWS_DEFAULT_REGION": config.ec2_region,
    }
    return aws_env


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_ssm_tunnel(config: SsmConfig, aws_env: dict[str, str], local_port: int) -> subprocess.Popen:
    logger.info("Opening SSM tunnel localhost:%d -> EC2:5432", local_port)
    proc = subprocess.Popen(
        [
            "aws", "ssm", "start-session",
            "--target", config.ec2_instance_id,
            "--region", config.ec2_region,
            "--document-name", "AWS-StartPortForwardingSession",
            "--parameters", f"portNumber=5432,localPortNumber={local_port}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=aws_env,
    )
    return proc


def wait_for_port(port: int, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except OSError:
            time.sleep(1)
    return False


def kill_tunnel(proc: subprocess.Popen) -> None:
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def ssm_send_command(config: SsmConfig, aws_env: dict[str, str], command: str, timeout: int = 60) -> str:
    import json

    result = _run([
        "aws", "ssm", "send-command",
        "--instance-ids", config.ec2_instance_id,
        "--region", config.ec2_region,
        "--document-name", "AWS-RunShellScript",
        "--parameters", json.dumps({"commands": [command]}),
        "--output", "json",
    ], env=aws_env, timeout=timeout)

    if result.returncode != 0:
        return f"send-command failed: {result.stderr[:200]}"

    cmd_id = json.loads(result.stdout)["Command"]["CommandId"]

    time.sleep(3)

    inv_result = _run([
        "aws", "ssm", "get-command-invocation",
        "--command-id", cmd_id,
        "--instance-id", config.ec2_instance_id,
        "--region", config.ec2_region,
        "--output", "json",
    ], env=aws_env, check=False, timeout=timeout)

    if inv_result.returncode != 0:
        return f"get-invocation failed: {inv_result.stderr[:200]}"

    inv = json.loads(inv_result.stdout)

    for _ in range(20):
        if inv.get("Status") in ("Success", "Failed", "TimedOut", "Cancelled"):
            break
        time.sleep(3)
        inv_result = _run([
            "aws", "ssm", "get-command-invocation",
            "--command-id", cmd_id,
            "--instance-id", config.ec2_instance_id,
            "--region", config.ec2_region,
            "--output", "json",
        ], env=aws_env, check=False, timeout=timeout)
        if inv_result.returncode == 0:
            inv = json.loads(inv_result.stdout)

    return f"Status: {inv.get('Status')}, Output: {inv.get('StandardOutputContent', '')[:200]}"


def get_db_password(config: SsmConfig, aws_env: dict[str, str]) -> str:
    result = _run([
        "aws", "ssm", "get-parameter",
        "--name", "${SSM_PASSWORD_PARAM}",
        "--with-decryption",
        "--region", config.ec2_region,
        "--query", "Parameter.Value",
        "--output", "text",
    ], env=aws_env, check=False)

    if result.returncode != 0:
        pytest.skip(f"Failed to fetch DB password: {result.stderr[:200]}")
    return result.stdout.strip()


def create_long_running_tunnel_script(
    config: SsmConfig,
    aws_env: dict[str, str],
    local_port: int,
    password_override: str | None = None,
) -> str:
    """Long-running pre-connect script for the [MCP] DB_URL/READY_TO_CONNECT protocol.

    Opens the SSM port-forward as a foreground child, waits for the local
    port to accept connections, fetches the DB password from SSM Parameter
    Store (or uses `password_override` if provided — useful for credential
    rotation tests), emits `[MCP] DB_URL postgresql://...` and
    `[MCP] READY_TO_CONNECT`, then `wait`s on the SSM child so that tunnel
    death propagates as script exit.
    """
    pw_override_block = ""
    if password_override is not None:
        # The test wants a deterministic password — bypass Parameter Store.
        pw_override_block = f'PW={password_override!r}\n'

    script_content = f"""#!/usr/bin/env bash
set -euo pipefail

export AWS_ACCESS_KEY_ID="{aws_env['AWS_ACCESS_KEY_ID']}"
export AWS_SECRET_ACCESS_KEY="{aws_env['AWS_SECRET_ACCESS_KEY']}"
export AWS_SESSION_TOKEN="{aws_env['AWS_SESSION_TOKEN']}"
export AWS_DEFAULT_REGION="{config.ec2_region}"

# Open the SSM tunnel as a backgrounded child of this shell.
aws ssm start-session \\
    --target "{config.ec2_instance_id}" \\
    --region "{config.ec2_region}" \\
    --document-name AWS-StartPortForwardingSession \\
    --parameters "portNumber=5432,localPortNumber={local_port}" \\
    >/dev/null 2>&1 &
TUNNEL_PID=$!

# Wait for the port to accept connections.
for _ in $(seq 1 30); do
    if nc -z 127.0.0.1 {local_port} 2>/dev/null; then
        break
    fi
    sleep 1
done

if ! nc -z 127.0.0.1 {local_port} 2>/dev/null; then
    kill "$TUNNEL_PID" 2>/dev/null || true
    echo "tunnel never came up" >&2
    exit 1
fi

# Resolve the DB password (Parameter Store or test override).
{pw_override_block if pw_override_block else 'PW=$(aws ssm get-parameter --name ${SSM_PASSWORD_PARAM} --with-decryption --query Parameter.Value --output text)'}

URL="postgresql://mcp_reader:${{PW}}@127.0.0.1:{local_port}/crm?connect_timeout=10&keepalives=1&keepalives_idle=30&keepalives_interval=10&keepalives_count=3"

printf '[MCP] DB_URL %s\\n' "$URL"
printf '[MCP] READY_TO_CONNECT\\n'

# Block until the SSM child dies, then exit so the MCP detects the
# tunnel loss within ~1s.
wait "$TUNNEL_PID"
"""
    fd, path = tempfile.mkstemp(suffix=".sh", prefix="ssm-long-running-")
    with os.fdopen(fd, "w") as f:
        f.write(script_content)
    os.chmod(path, stat.S_IRWXU)
    return path


def create_tunnel_script(config: SsmConfig, aws_env: dict[str, str], local_port: int) -> str:
    """Create a pre-connect script that opens an SSM tunnel."""
    script_content = f"""#!/bin/bash
set -e

export AWS_ACCESS_KEY_ID="{aws_env['AWS_ACCESS_KEY_ID']}"
export AWS_SECRET_ACCESS_KEY="{aws_env['AWS_SECRET_ACCESS_KEY']}"
export AWS_SESSION_TOKEN="{aws_env['AWS_SESSION_TOKEN']}"
export AWS_DEFAULT_REGION="{config.ec2_region}"

if nc -z 127.0.0.1 {local_port} 2>/dev/null; then
    exit 0
fi

aws ssm start-session \\
    --target "{config.ec2_instance_id}" \\
    --region "{config.ec2_region}" \\
    --document-name AWS-StartPortForwardingSession \\
    --parameters "portNumber=5432,localPortNumber={local_port}" \\
    >/dev/null 2>&1 &

TUNNEL_PID=$!

for i in $(seq 1 30); do
    if nc -z 127.0.0.1 {local_port} 2>/dev/null; then
        exit 0
    fi
    sleep 1
done

kill $TUNNEL_PID 2>/dev/null
exit 1
"""
    fd, path = tempfile.mkstemp(suffix=".sh", prefix="ssm-tunnel-")
    with os.fdopen(fd, "w") as f:
        f.write(script_content)
    os.chmod(path, stat.S_IRWXU)
    return path
