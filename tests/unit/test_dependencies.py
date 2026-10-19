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


def test_pglast_is_a_range_not_an_exact_pin():
    # The exact pin pglast==7.11 broke every fresh install when that
    # version was removed from PyPI (hotfix 0.1.6, found 2026-10-19).
    req = _requirement("pglast")
    assert "==" not in req, f"exact pin is fragile against PyPI removals: {req}"
    assert ">=" in req, f"missing lower bound: {req}"
    assert "<8" in req, f"missing upper bound below the untested 8.x line: {req}"
