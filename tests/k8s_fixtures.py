"""Helm-based PostgreSQL lifecycle for integration tests on Kubernetes."""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import time
from typing import Generator
from typing import Tuple

import pytest

logger = logging.getLogger(__name__)

NAMESPACE = "pgmcp-test"
RELEASE_NAME = "pgmcp-test-pg"
HELM_CHART = "bitnami/postgresql"
PG_PASSWORD = "testpass"
PG_DATABASE = "testdb"
PORT_FORWARD_TIMEOUT = 60
HELM_TIMEOUT = "300s"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(cmd: list[str], check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        logger.error("Command failed: %s\nstdout: %s\nstderr: %s", " ".join(cmd), result.stdout, result.stderr)
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
    return result


def _namespace_exists() -> bool:
    result = _run(["kubectl", "get", "namespace", NAMESPACE], check=False)
    return result.returncode == 0


def _release_exists() -> bool:
    result = _run(["helm", "list", "-n", NAMESPACE, "-q"], check=False)
    return RELEASE_NAME in (result.stdout or "")


def helm_install_postgres() -> None:
    if not _namespace_exists():
        _run(["kubectl", "create", "namespace", NAMESPACE])

    if _release_exists():
        logger.info("Release %s already exists, uninstalling first", RELEASE_NAME)
        _run(["helm", "uninstall", RELEASE_NAME, "-n", NAMESPACE, "--wait"], check=False)
        _wait_for_pod_gone()

    _run([
        "helm", "install", RELEASE_NAME, HELM_CHART,
        "-n", NAMESPACE,
        "--set", f"auth.postgresPassword={PG_PASSWORD}",
        "--set", f"auth.database={PG_DATABASE}",
        "--set", "primary.persistence.enabled=false",
        "--set", "primary.resources.requests.memory=128Mi",
        "--set", "primary.resources.requests.cpu=100m",
        "--set", "primary.resources.limits.memory=256Mi",
        "--wait",
        "--timeout", HELM_TIMEOUT,
    ])

    _wait_for_pod_ready()


def _wait_for_pod_ready(timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _run([
            "kubectl", "get", "pods", "-n", NAMESPACE,
            "-l", f"app.kubernetes.io/instance={RELEASE_NAME}",
            "-o", "jsonpath={.items[0].status.phase}",
        ], check=False)
        if result.stdout.strip() == "Running":
            logger.info("PostgreSQL pod is running")
            return
        time.sleep(3)
    pytest.skip(f"PostgreSQL pod not ready after {timeout}s")


def _wait_for_pod_gone(timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _run([
            "kubectl", "get", "pods", "-n", NAMESPACE,
            "-l", f"app.kubernetes.io/instance={RELEASE_NAME}",
            "-o", "jsonpath={.items}",
        ], check=False)
        if not result.stdout.strip() or result.stdout.strip() == "[]":
            return
        time.sleep(2)


def start_port_forward() -> Tuple[subprocess.Popen[str], int]:
    local_port = _find_free_port()
    svc_name = f"{RELEASE_NAME}-postgresql"

    proc = subprocess.Popen(
        ["kubectl", "port-forward", f"svc/{svc_name}", f"{local_port}:5432", "-n", NAMESPACE],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    deadline = time.time() + PORT_FORWARD_TIMEOUT
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=2):
                logger.info("Port forward ready on localhost:%d", local_port)
                return proc, local_port
        except OSError:
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr else ""
                pytest.skip(f"Port forward process died: {stderr}")
            time.sleep(1)

    proc.kill()
    pytest.skip(f"Port forward not ready after {PORT_FORWARD_TIMEOUT}s")
    raise AssertionError("unreachable")


def helm_uninstall_postgres() -> None:
    _run(["helm", "uninstall", RELEASE_NAME, "-n", NAMESPACE, "--wait"], check=False, timeout=120)
    _run(["kubectl", "delete", "namespace", NAMESPACE, "--ignore-not-found"], check=False, timeout=60)


def create_k8s_postgres() -> Generator[Tuple[str, str], None, None]:
    try:
        _run(["kubectl", "cluster-info"], timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("Kubernetes cluster not accessible")

    try:
        _run(["helm", "version", "--short"], timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("Helm not available")

    helm_install_postgres()

    proc, local_port = start_port_forward()
    connection_string = f"postgresql://postgres:{PG_PASSWORD}@localhost:{local_port}/{PG_DATABASE}"

    try:
        yield connection_string, "postgres:17"
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        helm_uninstall_postgres()
