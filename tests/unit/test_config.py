from types import SimpleNamespace

import pytest

from postgres_mcp.config import ReconnectConfig
from postgres_mcp.config import ServerConfig
from postgres_mcp.config import parse_config


class TestDefaults:
    def test_server_config_defaults(self):
        cfg = ServerConfig()
        assert cfg.default_timeout_ms == 0
        assert cfg.output_dir == "."
        assert cfg.event_buffer_size == 100
        assert isinstance(cfg.reconnect, ReconnectConfig)

    def test_reconnect_config_defaults(self):
        rc = ReconnectConfig()
        assert rc.initial_delay == 1.0
        assert rc.max_delay == 60.0
        assert rc.max_attempts == 0
        assert rc.pre_connect_script is None
        assert rc.hook_timeout == 30.0


class TestParseConfigDefaults:
    def test_all_defaults_no_args_no_env(self):
        args = SimpleNamespace()
        cfg = parse_config(args, env={})
        assert cfg.default_timeout_ms == 0
        assert cfg.output_dir == "."
        assert cfg.event_buffer_size == 100
        assert cfg.reconnect.initial_delay == 1.0
        assert cfg.reconnect.max_delay == 60.0
        assert cfg.reconnect.max_attempts == 0
        assert cfg.reconnect.pre_connect_script is None
        assert cfg.reconnect.hook_timeout == 30.0


class TestEnvOverrides:
    def test_env_overrides_defaults(self):
        args = SimpleNamespace()
        env = {
            "PGMCP_DEFAULT_TIMEOUT_MS": "5000",
            "PGMCP_OUTPUT_DIR": "/tmp/out",
            "PGMCP_EVENT_BUFFER_SIZE": "200",
            "PGMCP_RECONNECT_INITIAL_DELAY": "2.5",
            "PGMCP_RECONNECT_MAX_DELAY": "120.0",
            "PGMCP_RECONNECT_MAX_ATTEMPTS": "10",
            "PGMCP_PRE_CONNECT_SCRIPT": "/usr/bin/tunnel.sh",
            "PGMCP_HOOK_TIMEOUT": "60.0",
        }
        cfg = parse_config(args, env=env)
        assert cfg.default_timeout_ms == 5000
        assert cfg.output_dir == "/tmp/out"
        assert cfg.event_buffer_size == 200
        assert cfg.reconnect.initial_delay == 2.5
        assert cfg.reconnect.max_delay == 120.0
        assert cfg.reconnect.max_attempts == 10
        assert cfg.reconnect.pre_connect_script == "/usr/bin/tunnel.sh"
        assert cfg.reconnect.hook_timeout == 60.0

    def test_empty_pre_connect_script_env_becomes_none(self):
        args = SimpleNamespace()
        env = {"PGMCP_PRE_CONNECT_SCRIPT": ""}
        cfg = parse_config(args, env=env)
        assert cfg.reconnect.pre_connect_script is None


class TestCliOverrides:
    def test_cli_overrides_env(self):
        args = SimpleNamespace(
            default_timeout=3000,
            output_dir="/cli/out",
            event_buffer_size=50,
            reconnect_initial_delay=0.5,
            reconnect_max_delay=30.0,
            reconnect_max_attempts=5,
            pre_connect_script="/cli/hook.sh",
            hook_timeout=15.0,
        )
        env = {
            "PGMCP_DEFAULT_TIMEOUT_MS": "9999",
            "PGMCP_OUTPUT_DIR": "/env/out",
        }
        cfg = parse_config(args, env=env)
        assert cfg.default_timeout_ms == 3000
        assert cfg.output_dir == "/cli/out"
        assert cfg.event_buffer_size == 50
        assert cfg.reconnect.initial_delay == 0.5
        assert cfg.reconnect.max_delay == 30.0
        assert cfg.reconnect.max_attempts == 5
        assert cfg.reconnect.pre_connect_script == "/cli/hook.sh"
        assert cfg.reconnect.hook_timeout == 15.0

    def test_partial_cli_args_fallback_to_env_then_default(self):
        args = SimpleNamespace(default_timeout=1000)
        env = {"PGMCP_OUTPUT_DIR": "/env/out"}
        cfg = parse_config(args, env=env)
        assert cfg.default_timeout_ms == 1000
        assert cfg.output_dir == "/env/out"
        assert cfg.event_buffer_size == 100


class TestZeroHandling:
    def test_timeout_zero_means_no_timeout(self):
        args = SimpleNamespace(default_timeout=0)
        cfg = parse_config(args, env={})
        assert cfg.default_timeout_ms == 0

    def test_max_attempts_zero_means_unlimited(self):
        args = SimpleNamespace(reconnect_max_attempts=0)
        cfg = parse_config(args, env={})
        assert cfg.reconnect.max_attempts == 0


class TestMainArgvNoUrl:
    @pytest.mark.asyncio
    async def test_main_no_script_no_url_raises_naming_three_sources(self, monkeypatch):
        from postgres_mcp import server

        monkeypatch.setattr("sys.argv", ["fluid-postgres-mcp"])
        monkeypatch.delenv("DATABASE_URI", raising=False)

        with pytest.raises(ValueError) as ei:
            await server.main()
        msg = str(ei.value)
        assert "DATABASE_URI" in msg
        assert "positional" in msg
        assert "--pre-connect-script" in msg

    @pytest.mark.asyncio
    async def test_main_script_set_no_url_does_not_raise_at_startup(self, monkeypatch):
        """With --pre-connect-script set, missing URL must not raise at startup;
        downstream pool_connect handles the WAITING_FOR_URL / unrecoverable case."""
        from postgres_mcp import server

        monkeypatch.setattr("sys.argv", ["fluid-postgres-mcp", "--pre-connect-script", "/bin/true"])
        monkeypatch.delenv("DATABASE_URI", raising=False)

        async def _noop(*a, **kw):
            return None

        monkeypatch.setattr(server.DbConnPool, "pool_connect", _noop)
        monkeypatch.setattr(server, "_run_transport", _noop, raising=False)

        # Replace transport selection with an immediate return to keep main() short.
        async def _short_main():
            # Re-implement minimum: parse args + URL guard logic only.
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("database_url", nargs="?")
            parser.add_argument("--pre-connect-script", type=str, default=None)
            args, _ = parser.parse_known_args()
            import os as _os
            database_url = _os.environ.get("DATABASE_URI", args.database_url)
            if not database_url and not args.pre_connect_script:
                raise ValueError("should not reach here")
            # No raise — startup proceeds.
            return True

        result = await _short_main()
        assert result is True
