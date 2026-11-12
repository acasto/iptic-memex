"""Regression tests: _load_headers_from_command must not evaluate shell syntax.

Historically the function ran the configured command with shell=True, which
let a malicious headers_command value in config.ini inject arbitrary shell
(e.g. `echo {} ; touch /tmp/pwned`). The fix parses the command with
shlex.split and runs it without a shell, so shell metacharacters are passed
to the subprocess as literal characters/arguments instead of being executed.
"""

import os
import sys
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from memex_mcp.bootstrap import _load_headers_from_command


def test_plain_json_command_still_works(tmp_path):
    """A simple `cat <file>` command producing a JSON dict still loads."""
    payload = tmp_path / "headers.json"
    payload.write_text('{"Authorization": "Bearer abc123"}')
    headers = _load_headers_from_command(f"cat {payload}")
    assert headers == {"Authorization": "Bearer abc123"}


def test_shell_metacharacters_are_not_executed(tmp_path):
    """Injection payloads must NOT run: no shell means `;` is a literal argument."""
    marker = tmp_path / f"pwned-{uuid.uuid4().hex}"
    cmd = f"echo {{}} ; touch {marker}"
    # cat receives `{}` and `;` and `touch` and the path as literal argv,
    # prints them, json.loads fails, the function falls back to {}.
    headers = _load_headers_from_command(cmd)
    assert headers == {}
    assert not marker.exists(), "shell injection executed despite shlex fix"


def test_chaining_operator_no_longer_creates_second_file(tmp_path):
    """`&&` used to work under shell=True; it must now be inert, not executed."""
    marker = tmp_path / f"chain-{uuid.uuid4().hex}"
    cmd = f"echo {{}} && touch {marker}"
    headers = _load_headers_from_command(cmd)
    assert headers == {}
    assert not marker.exists()


def test_missing_command_fails_closed():
    """Nonexistent binaries raise inside the try and yield {} (no crash)."""
    assert _load_headers_from_command("definitely-not-a-real-binary-xyz") == {}


def test_non_dict_json_is_ignored(tmp_path):
    payload = tmp_path / "list.json"
    payload.write_text('["not", "a", "dict"]')
    assert _load_headers_from_command(f"cat {payload}") == {}
