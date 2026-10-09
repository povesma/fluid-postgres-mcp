"""Unit tests for split_command() — the --pre-connect-script tokenizer.

Both rule sets are exercised on every OS by passing `windows=` explicitly.
"""

from __future__ import annotations

import pytest

from postgres_mcp.sql.connection_script import split_command

COMPATIBLE_VALUES = [
    "/bin/cat",
    "/usr/bin/python3 /opt/tunnel.py db",
    "uv run --no-project --python 3.12 /opt/x/mcp-tunnel.py crm",
    "a\tb  c",
    "  leading and trailing  ",
]


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("value", COMPATIBLE_VALUES)
def test_values_without_quotes_or_backslashes_split_like_str_split(value, windows):
    assert split_command(value, windows=windows) == value.split()


def test_posix_newline_still_separates():
    assert split_command("a\nb", windows=False) == ["a", "b"]


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("value", ["a\x0cb", "a\xa0b"])
def test_non_ascii_whitespace_is_not_a_separator(value, windows):
    assert split_command(value, windows=windows) == [value]


def test_posix_quoted_path_with_space():
    assert split_command('"/path with space/s.py" db', windows=False) == ["/path with space/s.py", "db"]


def test_windows_quoted_path_with_space():
    value = 'uv run "C:\\Users\\Jane Doe\\x\\s.py" db'
    assert split_command(value, windows=True) == ["uv", "run", "C:\\Users\\Jane Doe\\x\\s.py", "db"]


def test_windows_unquoted_backslashes_are_kept():
    value = "uv run C:\\Users\\jane\\x\\s.py db"
    assert split_command(value, windows=True) == ["uv", "run", "C:\\Users\\jane\\x\\s.py", "db"]


def test_windows_empty_quotes_give_empty_argument():
    assert split_command('a "" b', windows=True) == ["a", "", "b"]


def test_windows_quoted_directory_ending_in_backslash():
    assert split_command('"C:\\dir\\" x', windows=True) == ["C:\\dir\\", "x"]


def test_windows_apostrophe_is_literal():
    assert split_command("it's", windows=True) == ["it's"]


def test_posix_apostrophe_is_a_quote():
    with pytest.raises(ValueError):
        split_command("it's", windows=False)


SECRET = "s3cr3t-pw"


@pytest.mark.parametrize(
    ("value", "windows"),
    [
        (f'tunnel.sh --password {SECRET} "unclosed', False),
        (f'tunnel.sh --password {SECRET} "unclosed', True),
        (f"tunnel.sh --password {SECRET} 'unclosed", False),
    ],
)
def test_unbalanced_quotes_raise_without_echoing_value(value, windows):
    with pytest.raises(ValueError) as excinfo:
        split_command(value, windows=windows)
    assert SECRET not in str(excinfo.value)
    assert "unclosed" not in str(excinfo.value)


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_empty_or_whitespace_only_raises(value, windows):
    with pytest.raises(ValueError):
        split_command(value, windows=windows)
