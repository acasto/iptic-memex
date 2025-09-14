from __future__ import annotations

import os
import sys
import json
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import types
from actions.assistant_cmd_tool_action import AssistantCmdToolAction
from actions.assistant_fs_handler_action import AssistantFsHandlerAction
from utils.filesystem_utils import FileSystemHandler
from utils.logging_utils import LoggingHandler


class DummyOutput:
    def write(self, *a, **k):
        pass
    def stop_spinner(self):
        pass


class DummyUtils:
    def __init__(self, logger):
        self.output = DummyOutput()
        self.fs = FileSystemHandler(config=None, output_handler=self.output)
        self.logger = logger
        self.input = types.SimpleNamespace(get_input=lambda *a, **k: "", get_bool=lambda *a, **k: False)


class DummyUI:
    def __init__(self):
        self.capabilities = types.SimpleNamespace(blocking=True)
    def emit(self, *a, **k):
        pass


class DummySession:
    def __init__(self, base_dir: str, logger: LoggingHandler):
        self._base_dir = base_dir
        self.utils = DummyUtils(logger)
        self.ui = DummyUI()
        self._contexts = []
        self._actions = {}

    def get_option(self, section: str, option: str, fallback=None):
        if section == 'TOOLS' and option == 'base_directory':
            return self._base_dir
        if section == 'TOOLS' and option == 'timeout':
            return 15
        return fallback

    def get_tools(self):
        return {'timeout': 15}

    def get_action(self, name: str):
        if name not in self._actions:
            if name == 'assistant_fs_handler':
                self._actions[name] = AssistantFsHandlerAction(self)
            elif name == 'count_tokens':
                self._actions[name] = types.SimpleNamespace(count_tiktoken=lambda s: len((s or '').split()))
            else:
                self._actions[name] = types.SimpleNamespace()
        return self._actions[name]

    def add_context(self, kind: str, value=None):
        self._contexts.append((kind, value))
        return value

    def set_flag(self, *a, **k):
        pass


class FakeConfig:
    def __init__(self, opts: dict):
        self._opts = opts
    def get_option(self, section: str, key: str, fallback=None):
        if section != 'LOG':
            return fallback
        return self._opts.get(key, fallback)


def _load_json_lines(path: Path):
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def test_cmd_tool_emits_exec_and_result_logs(tmp_path: Path):
    # Prepare logger in tmp log dir
    cfg = FakeConfig({
        'active': True,
        'dir': str(tmp_path),
        'per_run': True,
        'format': 'json',
        'log_cmd': 'detail',
    })
    logger = LoggingHandler(cfg, output_handler=None)

    # Session and cmd tool
    sess = DummySession(str(tmp_path), logger)
    sess.get_action('assistant_fs_handler')
    cmd = AssistantCmdToolAction(sess)

    cmd.run({'command': 'echo', 'arguments': 'hello'})

    # Validate logs
    files = list(tmp_path.glob('*.log'))
    assert files
    payloads = list(_load_json_lines(files[0]))
    kinds = [p.get('event') for p in payloads]
    assert 'cmd_exec' in kinds
    assert 'cmd_result' in kinds
    # Check that result recorded stdout length
    last = [p for p in payloads if p.get('event') == 'cmd_result'][-1]
    data = last.get('data') or {}
    assert data.get('stdout_len', 0) >= 5

