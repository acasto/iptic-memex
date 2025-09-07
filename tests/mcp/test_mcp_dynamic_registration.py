import os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session


def _make_session() -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    # Enable MCP feature flag
    sc.set_option('active', True)            # ensure defaults exist
    sc.set_option('enable_builtin_tools', '')
    sc.set_option('inactive_tools', '')
    sc.set_option('active_tools', '')        # clear allowlist so dynamic names are not filtered
    # Gate enabling: set [MCP].active=true via overrides path
    sc.set_option('active', True)
    sc.overrides['MCP'] = type('o', (), {'active': True})  # actions read via get_option('MCP','active')
    reg = ComponentRegistry(sc)
    sess = Session(sc, reg)
    # Provide minimal ui/utils to avoid output noise
    return sess


def test_demo_discover_register_and_specs():
    from actions.mcp_demo_action import McpDemoAction
    from actions.mcp_discover_action import McpDiscoverAction
    from actions.mcp_register_tools_action import McpRegisterToolsAction
    from actions.assistant_commands_action import AssistantCommandsAction

    sess = _make_session()

    # Load demo server (testmcp with two tools)
    McpDemoAction(sess).run()

    # Discover tools (no exception means ok)
    McpDiscoverAction(sess).run(['testmcp'])

    # Register tools for this session
    McpRegisterToolsAction(sess).run(['testmcp', '--alias'])

    # Build tool specs; ensure dynamic tools are present (sanitized names)
    act = AssistantCommandsAction(sess)
    specs_list = act.get_tool_specs()
    # Find entries by canonical_name (since API names are sanitized)
    idx_by_canonical = {s.get('canonical_name'): s for s in specs_list}
    assert 'mcp:testmcp/calc.sum' in idx_by_canonical
    assert 'mcp:testmcp/echo.say' in idx_by_canonical
    # Ensure parameters reflect schema
    calc_props = idx_by_canonical['mcp:testmcp/calc.sum']['parameters']['properties']
    assert 'a' in calc_props and 'b' in calc_props
    echo_props = idx_by_canonical['mcp:testmcp/echo.say']['parameters']['properties']
    assert 'text' in echo_props

    # Also ensure fixed args are wired in the command registry
    cmds = act.commands
    assert 'mcp:testmcp/calc.sum' in cmds
    fn = cmds['mcp:testmcp/calc.sum']['function']
    assert fn['name'] == 'mcp_proxy_tool'
    assert fn.get('fixed_args', {}).get('server') == 'testmcp'
    assert fn.get('fixed_args', {}).get('tool') == 'calc.sum'
