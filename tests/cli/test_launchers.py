"""Shell launchers must select the checkout environment without changing cwd."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / 'checkout with spaces'
    shutil.copytree(Path(__file__).resolve().parents[2] / 'scripts', root / 'scripts')
    python = root / '.venv' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    # A stand-in interpreter reports exactly what the wrapper passed through.
    python.write_text(
        '#!/bin/bash\n'
        'printf "%s\\0" "$PWD" "$@"\n'
        'if [[ "$*" == *"-f -"* ]]; then cat; fi\n'
    )
    python.chmod(0o755)
    return root


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shell wrappers')
@pytest.mark.parametrize('wrapper', ['memex', 'chat', 'ask'])
def test_symlinked_wrapper_preserves_cwd_and_arguments(checkout, tmp_path, wrapper):
    link = tmp_path / wrapper
    link.symlink_to(checkout / 'scripts' / wrapper)
    result = subprocess.run(
        [str(link), 'a question with spaces'], cwd=tmp_path,
        capture_output=True, check=True,
    )
    parts = result.stdout.decode().split('\0')
    assert parts[0] == str(tmp_path)
    assert parts[1] == str(checkout / 'main.py')
    if wrapper == 'memex':
        assert parts[2:] == ['a question with spaces', '']
    elif wrapper == 'chat':
        assert parts[2:] == ['-s', 'chat', 'a question with spaces', '']
    else:
        assert parts[2:] == ['-s', '-p', 'concise', '-f', '-', 'a question with spaces\n']


@pytest.mark.skipif(os.name == 'nt', reason='POSIX shell wrappers')
def test_missing_environment_reports_setup(checkout):
    (checkout / '.venv' / 'bin' / 'python').unlink()
    result = subprocess.run(
        [str(checkout / 'scripts' / 'memex'), '--help'], capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert 'uv sync --locked' in result.stderr
