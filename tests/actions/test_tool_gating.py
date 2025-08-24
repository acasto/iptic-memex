from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.assistant_commands_action import AssistantCommandsAction


class FakeSession:
    def __init__(self, opts: dict | None = None, agent: bool = False):
        self._opts = {('TOOLS', k): v for k, v in (opts or {}).items()}
        self._agent = agent
    def get_option(self, section, option, fallback=None):
        return self._opts.get((section, option), fallback)
    def get_action(self, name):
        # No user overrides
        return None
    def in_agent_mode(self):
        return bool(self._agent)


def _spec_names(sess: FakeSession) -> list[str]:
    act = AssistantCommandsAction(sess)
    specs = act.get_tool_specs()
    return sorted([s['name'] for s in specs])


def test_allowlist_limits_tools():
    sess = FakeSession({'active_tools': 'CMD,RAGSEARCH'})
    names = _spec_names(sess)
    assert names == ['cmd', 'ragsearch']


def test_denylist_excludes_when_no_allowlist():
    sess = FakeSession({'inactive_tools': 'FILE,WEBSEARCH'})
    names = _spec_names(sess)
    assert 'file' not in names and 'websearch' not in names
    # A couple of expected defaults remain
    assert 'cmd' in names and 'memory' in names


def test_agent_overrides():
    sess = FakeSession({'active_tools_agent': 'CMD,MEMORY'}, agent=True)
    names = _spec_names(sess)
    assert names == ['cmd', 'memory']

