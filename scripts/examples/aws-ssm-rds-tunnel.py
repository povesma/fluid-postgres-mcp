#!/usr/bin/env python3
"""AWS SSM → RDS-via-EC2-forwarder pre-connect script for
fluid-postgres-mcp.

Opens an SSM port-forwarding session that terminates at the RDS
endpoint via an EC2 acting as a pure SSM forwarder (no PG, no
userspace proxy on the EC2). Uses the
AWS-StartPortForwardingSessionToRemoteHost document. Emits the
stdout handshake fluid-postgres-mcp consumes, then supervises the
SSM child. Exit on child death triggers respawn.

Required env: EC2_INSTANCE_ID (the forwarder), EC2_REGION,
RDS_ENDPOINT (target RDS hostname), DB_NAME, DB_USERNAME, DB_PASSWD.
Optional env: DB_PORT (default 5432), ASSUME_ROLE_ARN, AWS_PROFILE.
Optional flag: --profile (overrides AWS_PROFILE).

Stdout protocol (everything else → stderr):
    [MCP] DB_URL postgresql://<user>:<pw>@localhost:<port>/<db>?...
    [MCP] READY_TO_CONNECT

Full docs (auth precedence, required IAM permissions, smoke test,
EC2-direct variant): README §AWS SSM examples.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from typing import Optional

SSM_AGENT_POLL_ATTEMPTS = 12
SSM_AGENT_POLL_INTERVAL = 10
TUNNEL_READY_TIMEOUT = 30
ROLE_SESSION_DURATION = 3600

_ssm_child: Optional[subprocess.Popen] = None
_ssm_session_id: Optional[str] = None
_ssm_region: Optional[str] = None
_ssm_env: Optional[dict[str, str]] = None


def log(msg: str) -> None:
    print(f"[ssm-tunnel] {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> "None":
    print(f"[ssm-tunnel] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aws-ssm-rds-tunnel.py",
        description=(
            "AWS SSM RDS-via-EC2-forwarder pre-connect script for "
            "fluid-postgres-mcp. Deployment-specific values come from "
            "environment variables; see the header docstring."
        ),
    )
    parser.add_argument(
        "--profile",
        default=None,
        help=(
            "AWS profile name. Overrides AWS_PROFILE env var. "
            "If neither is set, the SDK default credential chain is used."
        ),
    )
    return parser.parse_args()


def require_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        die(f"Missing required env var: {key}")
    return value


def run_aws(args: list[str], env: Optional[dict[str, str]] = None) -> str:
    cmd = ["aws", *args]
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        die("aws CLI not found in PATH; install the AWS CLI v2")
    if proc.returncode != 0:
        die(
            f"aws {' '.join(shlex.quote(a) for a in args)} failed "
            f"(exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def base_credentials(profile: Optional[str]) -> dict[str, str]:
    """Return a process env populated with credentials for the AWS SDK.

    Precedence: --profile > AWS_PROFILE > default chain. When no
    profile is selected, we hand back the current process env
    unchanged and let the AWS SDK pick credentials itself.
    """
    if profile is None:
        profile = os.environ.get("AWS_PROFILE", "").strip() or None

    env = os.environ.copy()
    if profile is None:
        log("No AWS profile selected; relying on SDK default credential chain")
        return env

    log(f"Resolving credentials for profile '{profile}'")
    out = run_aws(
        ["configure", "export-credentials", "--profile", profile, "--format", "process"],
        env=env,
    )
    try:
        creds = json.loads(out)
    except json.JSONDecodeError as e:
        die(
            f"Could not parse credentials for profile '{profile}': {e}. "
            f"Re-authenticate that profile (e.g. `aws login --profile {profile}`)."
        )
    env["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
    env["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
    if creds.get("SessionToken"):
        env["AWS_SESSION_TOKEN"] = creds["SessionToken"]
    # Profile is now resolved into env vars; clear AWS_PROFILE so
    # downstream `aws` calls don't double-resolve.
    env.pop("AWS_PROFILE", None)
    return env


def maybe_assume_role(env: dict[str, str], role_arn: Optional[str], region: str) -> dict[str, str]:
    if not role_arn:
        return env
    session_name = f"ssm-tunnel-{int(time.time())}"
    log(f"Assuming role {role_arn} as {session_name}")
    out = run_aws(
        [
            "sts", "assume-role",
            "--role-arn", role_arn,
            "--role-session-name", session_name,
            "--duration-seconds", str(ROLE_SESSION_DURATION),
            "--region", region,
            "--output", "json",
        ],
        env=env,
    )
    creds = json.loads(out)["Credentials"]
    new_env = env.copy()
    new_env["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
    new_env["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
    new_env["AWS_SESSION_TOKEN"] = creds["SessionToken"]
    identity = run_aws(
        ["sts", "get-caller-identity", "--region", region, "--output", "json"],
        env=new_env,
    )
    arn = json.loads(identity).get("Arn", "<unknown>")
    log(f"Assumed: {arn}")
    return new_env


def ensure_ec2_running(env: dict[str, str], instance_id: str, region: str) -> None:
    out = run_aws(
        [
            "ec2", "describe-instances",
            "--instance-ids", instance_id,
            "--region", region,
            "--query", "Reservations[0].Instances[0].State.Name",
            "--output", "text",
        ],
        env=env,
    )
    state = out.strip()
    log(f"EC2 {instance_id} state: {state}")
    if state == "running":
        return
    if state in ("stopped", "stopping", "pending"):
        if state == "stopping":
            log("EC2 is stopping — waiting before restart")
            run_aws(
                ["ec2", "wait", "instance-stopped", "--instance-ids", instance_id, "--region", region],
                env=env,
            )
        if state in ("stopped", "stopping"):
            log("Starting EC2")
            run_aws(
                ["ec2", "start-instances", "--instance-ids", instance_id, "--region", region],
                env=env,
            )
        log("Waiting for EC2 to reach 'running'")
        run_aws(
            ["ec2", "wait", "instance-running", "--instance-ids", instance_id, "--region", region],
            env=env,
        )
        return
    die(f"EC2 {instance_id} is in unexpected state: {state}")


def wait_ssm_agent(env: dict[str, str], instance_id: str, region: str) -> None:
    log("Waiting for SSM agent to be online")
    for attempt in range(1, SSM_AGENT_POLL_ATTEMPTS + 1):
        out = run_aws(
            [
                "ssm", "describe-instance-information",
                "--region", region,
                "--filters", f"Key=InstanceIds,Values={instance_id}",
                "--query", "length(InstanceInformationList)",
                "--output", "text",
            ],
            env=env,
        )
        try:
            count = int(out.strip())
        except ValueError:
            count = 0
        if count >= 1:
            log("SSM agent online")
            return
        if attempt == SSM_AGENT_POLL_ATTEMPTS:
            die("SSM agent not online after 2 minutes")
        time.sleep(SSM_AGENT_POLL_INTERVAL)


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_ssm_tunnel(
    env: dict[str, str],
    instance_id: str,
    region: str,
    local_port: int,
    remote_host: str,
    remote_port: int,
) -> tuple[subprocess.Popen, Optional[str]]:
    log(f"Opening SSM tunnel (localhost:{local_port} -> {remote_host}:{remote_port} via {instance_id})")
    # RDS-via-EC2-forwarder: the SSM session terminates on the EC2,
    # which forwards traffic to remote_host (the RDS endpoint).
    # No PG and no userspace proxy run on the EC2.
    params = f"host={remote_host},portNumber={remote_port},localPortNumber={local_port}"
    proc = subprocess.Popen(
        [
            "aws", "ssm", "start-session",
            "--target", instance_id,
            "--region", region,
            "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
            "--parameters", params,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        text=True,
    )
    session_id = _read_session_id(proc)
    if session_id is None:
        log("Warning: could not parse SSM session id; remote-side cleanup may be incomplete")
    else:
        log(f"SSM session id: {session_id}")
    return proc, session_id


def _read_session_id(proc: subprocess.Popen) -> Optional[str]:
    if proc.stdout is None:
        return None
    deadline = time.time() + 15
    session_id: Optional[str] = None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                return session_id
            continue
        log(f"ssm: {line.rstrip()}")
        marker = "Starting session with SessionId: "
        idx = line.find(marker)
        if idx >= 0:
            session_id = line[idx + len(marker):].strip()
            break
    return session_id


def terminate_remote_session() -> None:
    global _ssm_session_id, _ssm_region, _ssm_env
    if _ssm_session_id is None or _ssm_region is None or _ssm_env is None:
        return
    log(f"Terminating remote SSM session {_ssm_session_id}")
    proc = subprocess.run(
        ["aws", "ssm", "terminate-session", "--session-id", _ssm_session_id, "--region", _ssm_region],
        env=_ssm_env, capture_output=True, text=True, timeout=10, check=False,
    )
    if proc.returncode != 0:
        log(f"terminate-session failed: {proc.stderr.strip()}")


def terminate_ssm_child(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    try:
        pgid = os.getpgid(child.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        try:
            child.terminate()
        except ProcessLookupError:
            return
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            pgid = os.getpgid(child.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                child.kill()
            except ProcessLookupError:
                pass


def wait_port_open(port: int, timeout: int = TUNNEL_READY_TIMEOUT) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(1)
    die(f"Tunnel not ready after {timeout}s on localhost:{port}")


def probe_postgres(local_port: int, db: str, user: str, password: str) -> None:
    log("Probing PostgreSQL via uv run psycopg")
    dsn = f"host=127.0.0.1 port={local_port} dbname={db} user={user} password={password} connect_timeout=10"
    script = (
        "import sys, psycopg\n"
        "dsn = sys.stdin.read()\n"
        "with psycopg.connect(dsn) as conn:\n"
        "    with conn.cursor() as cur:\n"
        "        cur.execute('SELECT 1')\n"
        "        cur.fetchone()\n"
    )
    proc = subprocess.run(
        ["uv", "run", "--quiet", "--with", "psycopg[binary]", "python", "-c", script],
        input=dsn, capture_output=True, text=True, check=False, timeout=60,
    )
    if proc.returncode != 0:
        die(
            f"PostgreSQL probe failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    log("PostgreSQL reachable")


def emit_handshake(local_port: int, db: str, user: str, password: str) -> None:
    qs = (
        "connect_timeout=10&keepalives=1&keepalives_idle=30"
        "&keepalives_interval=10&keepalives_count=3"
    )
    url = f"postgresql://{user}:{password}@localhost:{local_port}/{db}?{qs}"
    sys.stdout.write(f"[MCP] DB_URL {url}\n")
    sys.stdout.write("[MCP] READY_TO_CONNECT\n")
    sys.stdout.flush()


def install_signal_handlers() -> None:
    def handler(signum, _frame):
        log(f"Received signal {signum}; tearing down tunnel")
        terminate_remote_session()
        global _ssm_child
        if _ssm_child is not None:
            terminate_ssm_child(_ssm_child)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def supervise(child: subprocess.Popen) -> None:
    log("Tunnel up; supervising SSM child")
    rc = child.wait()
    terminate_remote_session()
    die(f"SSM child exited with status {rc}; tunnel is down", code=1)


def main() -> None:
    args = parse_args()
    install_signal_handlers()

    instance_id = require_env("EC2_INSTANCE_ID")
    region = require_env("EC2_REGION")
    rds_endpoint = require_env("RDS_ENDPOINT")
    db_name = require_env("DB_NAME")
    db_user = require_env("DB_USERNAME")
    db_pass = require_env("DB_PASSWD")
    db_port = int(os.environ.get("DB_PORT", "5432"))
    role_arn = os.environ.get("ASSUME_ROLE_ARN", "").strip() or None

    base_env = base_credentials(args.profile)
    env = maybe_assume_role(base_env, role_arn, region)

    ensure_ec2_running(env, instance_id, region)
    wait_ssm_agent(env, instance_id, region)

    local_port = pick_free_port()
    log(f"Reserved local port {local_port}")

    global _ssm_child, _ssm_session_id, _ssm_region, _ssm_env
    _ssm_region = region
    _ssm_env = env
    _ssm_child, _ssm_session_id = start_ssm_tunnel(
        env, instance_id, region, local_port,
        remote_host=rds_endpoint,
        remote_port=db_port,
    )
    wait_port_open(local_port)
    probe_postgres(local_port, db_name, db_user, db_pass)

    emit_handshake(local_port, db_name, db_user, db_pass)
    supervise(_ssm_child)


if __name__ == "__main__":
    main()
