import os, sys, re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session


def _make_session():
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    sc.set_option('active_tools', '')
    sc.set_option('inactive_tools', '')
    # Gate MCP
    sc.overrides['MCP'] = type('o', (), {'active': True})
    return Session(sc, ComponentRegistry(sc))


def test_sanitized_names_and_mapping_are_stored():
    from actions.mcp_demo_action import McpDemoAction
    from actions.mcp_register_tools_action import McpRegisterToolsAction
    from actions.assistant_commands_action import AssistantCommandsAction

    sess = _make_session()
    # Load demo and register dynamic tools with aliases
    McpDemoAction(sess).run()
    McpRegisterToolsAction(sess).run(['testmcp', '--alias'])

    act = AssistantCommandsAction(sess)
    specs = act.get_tool_specs()
    # Names returned to providers must be API-safe
    for s in specs:
        name = s.get('name') or ''
        assert re.match(r'^[A-Za-z0-9_-]+$', name) is not None

    # Mapping is present for translating API name -> canonical
    mapping = sess.get_user_data('__tool_api_to_cmd__')
    assert isinstance(mapping, dict) and mapping
    # Expect at least one mapping to the canonical MCP name
    assert any(v in ('mcp:testmcp/calc.sum', 'mcp:testmcp/echo.say') for v in mapping.values())

