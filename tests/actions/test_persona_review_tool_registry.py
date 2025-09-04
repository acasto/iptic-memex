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


def _make_session() -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    # Allowlist only persona_review for this test to ensure gating works
    sc.set_option('active_tools', 'persona_review')
    sc.set_option('inactive_tools', '')
    reg = ComponentRegistry(sc)
    sess = Session(sc, reg)
    return sess


def test_persona_review_tool_appears_in_commands_when_active():
    sess = _make_session()
    act = AssistantCommandsAction(sess)
    names = sorted(list((act.commands or {}).keys()))
    # Expect persona_review present when allowlisted
    assert 'persona_review' in names

