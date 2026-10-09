"""Guards on declared runtime dependencies in pyproject.toml."""

import re
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _requirement(name: str) -> str:
    text = PYPROJECT.read_text()
    match = re.search(rf'^\s*"({re.escape(name)}(\[[^\]]*\])?[^"]*)"', text, re.MULTILINE)
    assert match, f"{name} not declared in pyproject.toml"
    return match.group(1)


def test_mcp_is_pinned_below_2():
    # mcp 2.x removed mcp.server.fastmcp, which server.py imports.
    assert "<2" in _requirement("mcp")
