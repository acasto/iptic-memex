from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.assistant_commands_action import AssistantCommandsAction
from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session


def _make_session(opts: dict | None = None, agent: bool = False) -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    # Apply overrides for tool gating directly into session overrides
    # Start from a clean slate so user config doesn't leak into tests
    sc.set_option('active_tools', '')
    sc.set_option('inactive_tools', '')
    for k, v in (opts or {}).items():
        sc.set_option(k, v)
    reg = ComponentRegistry(sc)
    sess = Session(sc, reg)
    if agent:
        sess.enter_agent_mode('deny')
    return sess


def _spec_names(sess: Session) -> list[str]:
    act = AssistantCommandsAction(sess)
    specs = act.get_tool_specs()
    return sorted([s['name'] for s in specs])


def test_allowlist_limits_tools():
    sess = _make_session({'active_tools': 'CMD,RAGSEARCH'})
    names = _spec_names(sess)
    assert names == ['cmd', 'ragsearch']


def test_denylist_excludes_when_no_allowlist():
    sess = _make_session({'inactive_tools': 'FILE,WEBSEARCH'})
    names = _spec_names(sess)
    assert 'file' not in names and 'websearch' not in names
    # A couple of expected defaults remain
    assert 'cmd' in names and 'memory' in names


def test_agent_overrides():
    sess = _make_session({'active_tools_agent': 'CMD,MEMORY'}, agent=True)
    names = _spec_names(sess)
    assert names == ['cmd', 'memory']
