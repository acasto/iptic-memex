from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import types

from actions.assistant_fs_handler_action import AssistantFsHandlerAction
from actions.assistant_cmd_tool_action import AssistantCmdToolAction
from actions.assistant_docker_tool_action import AssistantDockerToolAction
from utils.filesystem_utils import FileSystemHandler


class DummyOutput:
    def write(self, *a, **k):
        pass
    def stop_spinner(self):
        pass
    def info(self, *a, **k):
        pass
    def warning(self, *a, **k):
        pass
    def error(self, *a, **k):
        pass
    def style_text(self, t, *_):
        return t


class DummyUtils:
    def __init__(self):
        self.output = DummyOutput()
        self.input = types.SimpleNamespace(get_input=lambda *a, **k: "", get_bool=lambda *a, **k: False)
        self.fs = FileSystemHandler(config=None, output_handler=self.output)


class DummyUI:
    def __init__(self):
        self.capabilities = types.SimpleNamespace(blocking=True)
    def emit(self, *a, **k):
        pass
    def ask_bool(self, *a, **k):
        return True


class DummySession:
    def __init__(self, base_dir: str):
        self._base_dir = base_dir
        self.utils = DummyUtils()
        self.ui = DummyUI()
        self._contexts = []
        self._actions = {}

    # Minimal config accessors used by actions
    def get_option(self, section: str, option: str, fallback=None):
        if section == 'TOOLS' and option == 'base_directory':
            return self._base_dir
        if section == 'TOOLS' and option == 'timeout':
            return 15
        if section == 'EPHEMERAL' and option == 'mount_readonly':
            return False
        return fallback

    def get_tools(self):
        return {
            'large_input_limit': 4000,
            'confirm_large_input': True,
            'timeout': 15,
            # default docker env
            'docker_env': 'ephemeral',
        }

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


def test_fs_handler_resolves_relative_paths_inside_base_dir(tmp_path: Path):
    base_dir = str(tmp_path)
    sess = DummySession(base_dir)
    fs = AssistantFsHandlerAction(sess)

    # Create a file inside base dir
    inside = tmp_path / 'sub' / 'file.txt'
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text('hello world', encoding='utf-8')

    # Should validate and read
    resolved = fs.resolve_path('sub/file.txt')
    assert resolved is not None
    assert resolved == str(inside)
    content = fs.read_file('sub/file.txt', binary=False)
    assert content == 'hello world'


def test_fs_handler_blocks_outside_paths(tmp_path: Path):
    base_dir = str(tmp_path)
    sess = DummySession(base_dir)
    fs = AssistantFsHandlerAction(sess)

    # Create a file outside the base dir
    outside_dir = tempfile.mkdtemp()
    outside_file = os.path.join(outside_dir, 'secret.txt')
    with open(outside_file, 'w', encoding='utf-8') as f:
        f.write('nope')

    # Attempt to access via .. should be blocked
    rel_outside = os.path.relpath(outside_file, start=base_dir)
    assert '..' in rel_outside or rel_outside.startswith('..')
    assert fs.resolve_path(rel_outside) is None


def test_cmd_tool_runs_with_cwd_set_to_base_dir(tmp_path: Path):
    base_dir = str(tmp_path)
    # Put a marker file in base dir to detect ls
    (tmp_path / 'marker.txt').write_text('x', encoding='utf-8')

    sess = DummySession(base_dir)
    # Ensure fs handler is created for validation usage
    sess.get_action('assistant_fs_handler')
    cmd = AssistantCmdToolAction(sess)

    # Run pwd; expect base_dir in output
    cmd.run({'command': 'pwd'})
    outputs = [v for (k, v) in sess._contexts if k == 'assistant' and isinstance(v, dict) and v.get('name') == 'command_output']
    assert any(base_dir in (o.get('content') or '') for o in outputs)

    # Run ls and expect to see marker.txt listed
    sess._contexts.clear()
    cmd.run({'command': 'ls'})
    outputs = [v for (k, v) in sess._contexts if k == 'assistant' and isinstance(v, dict) and v.get('name') == 'command_output']
    assert any('marker.txt' in (o.get('content') or '') for o in outputs)


def test_docker_tool_mounts_base_dir_without_running_docker(tmp_path: Path):
    base_dir = str(tmp_path)
    sess = DummySession(base_dir)
    # Action will read config; we only test command creation (no docker execution)
    docker = AssistantDockerToolAction(sess)

    cmdline = docker.create_docker_command('echo hello')
    # Should include the -v <base_dir>:/workspace mount and working directory
    assert '-v' in cmdline
    assert any(arg.startswith(f"{base_dir}:/workspace") for arg in cmdline)
    # For ephemeral env, run command is `docker run --rm ... -w /workspace <image> /bin/bash -c ...`
    assert '-w' in cmdline and '/workspace' in cmdline

