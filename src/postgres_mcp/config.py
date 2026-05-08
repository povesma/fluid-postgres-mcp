from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field
from typing import Optional


@dataclass
class ReconnectConfig:
    initial_delay: float = 1.0
    max_delay: float = 60.0
    max_attempts: int = 0
    pre_connect_script: Optional[str] = None
    hook_timeout: float = 30.0


@dataclass
class ServerConfig:
    default_timeout_ms: int = 0
    output_dir: str = "."
    event_buffer_size: int = 100
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)


def parse_config(args: object, env: Optional[dict[str, str]] = None) -> ServerConfig:
    if env is None:
        env = dict(os.environ)

    def _get(attr: str, env_key: str, default: str, cast: type = str):
        cli_val = getattr(args, attr, None)
        if cli_val is not None:
            return cast(cli_val)
        env_val = env.get(env_key)
        if env_val is not None:
            return cast(env_val)
        return cast(default)

    reconnect = ReconnectConfig(
        initial_delay=_get("reconnect_initial_delay", "PGMCP_RECONNECT_INITIAL_DELAY", "1.0", float),
        max_delay=_get("reconnect_max_delay", "PGMCP_RECONNECT_MAX_DELAY", "60.0", float),
        max_attempts=_get("reconnect_max_attempts", "PGMCP_RECONNECT_MAX_ATTEMPTS", "0", int),
        pre_connect_script=_get("pre_connect_script", "PGMCP_PRE_CONNECT_SCRIPT", "", str) or None,
        hook_timeout=_get("hook_timeout", "PGMCP_HOOK_TIMEOUT", "30.0", float),
    )

    return ServerConfig(
        default_timeout_ms=_get("default_timeout", "PGMCP_DEFAULT_TIMEOUT_MS", "0", int),
        output_dir=_get("output_dir", "PGMCP_OUTPUT_DIR", ".", str),
        event_buffer_size=_get("event_buffer_size", "PGMCP_EVENT_BUFFER_SIZE", "100", int),
        reconnect=reconnect,
    )
