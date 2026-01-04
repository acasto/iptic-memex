from __future__ import annotations

import os
import sys
from pathlib import Path
import types

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.assistant_docker_tool_action import AssistantDockerToolAction


class DummyOutput:
    def write(self, *a, **k):
        pass
    def stop_spinner(self):
        pass
    def style_text(self, t, *_):
        return t


class DummyUtils:
    def __init__(self):
        self.output = DummyOutput()
        self.input = types.SimpleNamespace(get_input=lambda *a, **k: "", get_bool=lambda *a, **k: False)


class DummyUI:
    def __init__(self):
        self.capabilities = types.SimpleNamespace(blocking=True)
    def emit(self, *a, **k):
        pass


class DummySession:
    def __init__(self, base_dir: str, tools_env: str, agent_ephemeral: bool):
        self._base_dir = base_dir
        self._tools_env = tools_env
        self._agent_ephemeral = agent_ephemeral
        self.utils = DummyUtils()
        self.ui = DummyUI()
        self.session_uid = 'dummy-session'
        self.user_data = {'session_uid': self.session_uid}
        self._cleanup_callbacks = []

    def in_agent_mode(self) -> bool:
        return True

    def get_option(self, section: str, option: str, fallback=None):
        if section == 'TOOLS' and option == 'base_directory':
            return self._base_dir
        if section == 'AGENT' and option == 'docker_always_ephemeral':
            return self._agent_ephemeral
        # Simulate a persistent env when asked
        if section.upper() == self._tools_env.upper() and option == 'persistent':
            return True
        if section.upper() == self._tools_env.upper() and option == 'docker_image':
            return 'ubuntu:latest'
        return fallback

    def get_tools(self):
        return {'docker_env': self._tools_env}

    def get_action(self, name: str):
        if name == 'count_tokens':
            return types.SimpleNamespace(count_tiktoken=lambda s: len((s or '').split()))
        if name == 'assistant_fs_handler':
            return types.SimpleNamespace()
        return types.SimpleNamespace()

    def register_cleanup_callback(self, callback):
        self._cleanup_callbacks.append(callback)


def test_agent_forces_ephemeral_even_when_tools_requests_persistent(tmp_path: Path):
    sess = DummySession(str(tmp_path), tools_env='webdev', agent_ephemeral=True)
    docker = AssistantDockerToolAction(sess)
    cmdline = docker.create_docker_command('echo hello')
    # Expect docker run --rm (ephemeral) not exec
    assert cmdline[0:3] == ['docker', 'run', '--rm']
    # Base dir mounted and working directory set
    assert '-v' in cmdline and '-w' in cmdline and str(tmp_path) in ' '.join(cmdline)


def test_agent_can_opt_out_and_use_persistent(tmp_path: Path):
    sess = DummySession(str(tmp_path), tools_env='webdev', agent_ephemeral=False)
    docker = AssistantDockerToolAction(sess)
    cmdline = docker.create_docker_command('echo hello')
    # When not forcing ephemeral and env is persistent, expect docker exec form
    assert cmdline[0:2] == ['docker', 'exec']
