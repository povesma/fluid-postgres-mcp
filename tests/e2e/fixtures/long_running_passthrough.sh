#!/usr/bin/env bash
#
# Long-running pre-connect-script test fixture.
# Reads the target URL from $LONG_RUNNING_URL, prints it as a
# `[MCP] DB_URL` line, signals readiness, then sleeps until SIGTERM.
#
# Uses `exec sleep <large>` so the bash shell is replaced by sleep —
# SIGTERM goes directly to sleep which exits cleanly. The naïve
# `trap 'exit 0' TERM; while true; do sleep 60 & wait $!; done`
# pattern is unreliable on macOS: the parent's
# asyncio.subprocess.Process.wait() does not always observe SIGCHLD
# when bash forks a backgrounded sleep, leaving the manager unaware
# that the script has exited. Replacing the bash process with sleep
# ensures the asyncio child watcher sees the exit.
#
set -euo pipefail

if [[ -z "${LONG_RUNNING_URL:-}" ]]; then
    echo "LONG_RUNNING_URL env var is required" >&2
    exit 2
fi

printf '[MCP] DB_URL %s\n' "$LONG_RUNNING_URL"
printf '[MCP] READY_TO_CONNECT\n'

# macOS BSD `sleep` does not accept "infinity"; pass a very large
# integer (~68 years) instead.
exec sleep 2147483647
