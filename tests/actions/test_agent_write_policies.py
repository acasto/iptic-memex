from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import types
from actions.assistant_file_tool_action import AssistantFileToolAction


class FakeOutput:
    def write(self, *a, **k):
        pass
    def stop_spinner(self):
        pass


class FakeUtils:
    def __init__(self):
        self.output = FakeOutput()
        self.input = types.SimpleNamespace(get_input=lambda **k: "", get_bool=lambda **k: False)
        self.fs = types.SimpleNamespace()


class FakeUI:
    def __init__(self):
        self.capabilities = types.SimpleNamespace(blocking=True)
    def emit(self, *a, **k):
        pass
    def ask_bool(self, *a, **k):
        return False


class FakeFsHandler:
    def __init__(self):
        self.writes = []
        self.appends = []
        self.reads = {}
        self.resolved = {}
    def resolve_path(self, path, must_exist=False):
        if must_exist and path not in self.resolved:
            raise TypeError("must exist")
        return self.resolved.get(path, path)
    def read_file(self, path, **k):
        return self.reads.get(path, "")
    def write_file(self, path, content, create_dirs=False, append=False, force=False, **k):
        if append:
            self.appends.append((path, content, force))
        else:
            self.writes.append((path, content, force))
        return True
    @staticmethod
    def _generate_diff(original, new, filename):
        return f"--- {filename} (original)\n+++ {filename} (new)\n-OLD\n+NEW"


class FakeTokenCounter:
    @staticmethod
    def count_tiktoken(text):
        return len((text or "").split())


class FakeSession:
    def __init__(self, policy: str | None):
        self.utils = FakeUtils()
        self.ui = FakeUI()
        self._policy = policy
        self._actions = {
            'assistant_fs_handler': FakeFsHandler(),
            'count_tokens': FakeTokenCounter(),
            'memex_runner': types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout="", returncode=0, stderr="")),
        }
        self._tools = {'large_input_limit': 4000, 'confirm_large_input': True}
        self._contexts = []
    def get_action(self, name):
        return self._actions[name]
    def get_tools(self):
        return dict(self._tools)
    def get_agent_write_policy(self):
        return self._policy
    def add_context(self, kind, value=None):
        self._contexts.append((kind, value))
        return types.SimpleNamespace(file=value) if kind == 'file' else value
    # Stubs required by action but unused in these tests
    def ui_emit(self, *a, **k):
        pass


def _get_ctx_msgs(sess: FakeSession, name: str):
    return [v for (k, v) in sess._contexts if k == 'assistant' and isinstance(v, dict) and v.get('name') == name]


def test_write_policy_deny_blocks_write_and_prompts_diff_instruction():
    sess = FakeSession('deny')
    action = AssistantFileToolAction(sess)
    args = {'mode': 'write', 'file': 'demo.txt'}
    action.start(args, content='new content')
    # No write performed
    assert sess.get_action('assistant_fs_handler').writes == []
    # Assistant context contains policy message
    msgs = _get_ctx_msgs(sess, 'file_tool_result')
    assert any('Writes are disabled by policy' in (m.get('content') or '') for m in msgs)


def test_write_policy_dry_run_outputs_diff_without_writing():
    sess = FakeSession('dry-run')
    # Provide original content to diff against
    fs = sess.get_action('assistant_fs_handler')
    fs.reads['demo.txt'] = 'old content'
    fs.resolved['demo.txt'] = 'demo.txt'
    action = AssistantFileToolAction(sess)
    args = {'mode': 'write', 'file': 'demo.txt'}
    action.start(args, content='new content')
    # No write performed
    assert fs.writes == []
    # Assistant context contains a diff
    msgs = _get_ctx_msgs(sess, 'file_tool_result')
    assert any('+++' in (m.get('content') or '') for m in msgs)


def test_write_policy_allow_performs_write_without_confirmation():
    sess = FakeSession('allow')
    action = AssistantFileToolAction(sess)
    args = {'mode': 'write', 'file': 'demo.txt'}
    action.start(args, content='new content')
    # Write performed with force=True
    writes = sess.get_action('assistant_fs_handler').writes
    assert len(writes) == 1 and writes[0][0] == 'demo.txt' and writes[0][2] is True
    # Assistant context confirms write
    msgs = _get_ctx_msgs(sess, 'file_tool_result')
    assert any('written successfully' in (m.get('content') or '').lower() for m in msgs)

