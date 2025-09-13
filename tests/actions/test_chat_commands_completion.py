import os
import sys

import pytest

# Ensure project root on sys.path for 'actions' imports
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.chat_commands_action import ChatCommandsAction


class _StubRegistry:
    def __init__(self):
        self._specs = {
            'commands': [
                {
                    'command': 'file',
                    'subs': [
                        {
                            'sub': '',
                            'type': 'action',
                            'action': 'load_file',
                            'method': None,
                            'args': [],
                            'complete': {'type': 'builtin', 'name': 'file_paths'},
                            'ui': {},
                        }
                    ],
                },
                {
                    'command': 'load',
                    'subs': [
                        {
                            'sub': 'file',
                            'type': 'action',
                            'action': 'load_file',
                            'method': None,
                            'args': [],
                            'complete': {'type': 'builtin', 'name': 'file_paths'},
                            'ui': {},
                        }
                    ],
                },
            ]
        }

    def get_specs(self, mode):
        return self._specs


class _StubSession:
    def get_action(self, name):
        if name == 'user_commands_registry':
            return _StubRegistry()
        return None


@pytest.fixture()
def stub_fs(monkeypatch):
    # Provide a stable directory listing and dir detection
    entries = ['alpha.txt', 'beta', 'gamma.md']

    def fake_listdir(path):
        return list(entries)

    def fake_isdir(p):
        # Treat 'beta' as a directory; others as files
        base = os.path.basename(p.rstrip('/'))
        return base == 'beta'

    monkeypatch.setattr(os, 'listdir', fake_listdir)
    monkeypatch.setattr(os.path, 'isdir', fake_isdir)


def test_default_subcommand_file_paths_completion(stub_fs):
    sess = _StubSession()
    a = ChatCommandsAction(sess)
    # Simulate completing after '/file '
    line = '/file '
    out = a.complete(line, len(line), '')
    # Expect file path suggestions (dirs with trailing slash)
    assert 'alpha.txt' in out
    assert 'beta/' in out
    assert 'gamma.md' in out


def test_explicit_load_file_arg_completion(stub_fs):
    sess = _StubSession()
    a = ChatCommandsAction(sess)
    # Simulate completing after '/load file '
    line = '/load file '
    out = a.complete(line, len(line), '')
    assert 'alpha.txt' in out
    assert 'beta/' in out
    assert 'gamma.md' in out


def test_subcommand_completion_appends_space():
    sess = _StubSession()
    a = ChatCommandsAction(sess)
    # Completing subcommand name should include a trailing space
    out = a.complete('/load f', len('/load f'), 'f')
    assert 'file ' in out
    assert 'file' not in [o for o in out if o != 'file ']


def test_default_subcommand_file_paths_completion_while_typing(stub_fs):
    sess = _StubSession()
    a = ChatCommandsAction(sess)
    # Simulate typing an argument for a default-only command: '/file a'
    line = '/file a'
    out = a.complete(line, len(line), 'a')
    # Should still complete file paths while typing, not try subcommands
    assert 'alpha.txt' in out
