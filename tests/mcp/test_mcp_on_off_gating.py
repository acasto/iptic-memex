import os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_manager import ConfigManager
from core.session_builder import SessionBuilder


def _builder():
    cfg = ConfigManager()
    return cfg, SessionBuilder(cfg)


def test_mcp_on_off_gating(monkeypatch):
    # Disable MCP in base config initially
    cfg, builder = _builder()
    sc = cfg.create_session_config()
    base = sc.base_config
    if not base.has_section('MCP'):
        base.add_section('MCP')
    base.set('MCP', 'active', 'false')

    sess = builder.build(mode='chat')

    from actions.user_commands_action import UserCommandsAction

    act = UserCommandsAction(sess)
    # Completion after '/mcp ' should list only valid subs
    subs = set(act.complete('/mcp ', len('/mcp '), ''))
    assert 'on' in subs
    assert 'off' not in subs

    # Enable MCP
    from actions.mcp_toggle_action import McpToggleAction
    McpToggleAction(sess).run(['on'])

    # Rebuild commands to refresh gating
    act2 = UserCommandsAction(sess)
    subs2 = set(act2.complete('/mcp ', len('/mcp '), ''))
    assert 'off' in subs2
    assert 'on' not in subs2
