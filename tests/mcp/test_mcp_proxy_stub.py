import os, sys, json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session


def _make_session() -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    sc.set_option('inactive_tools', '')
    # Enable [MCP].active gating for actions
    sc.overrides['MCP'] = type('o', (), {'active': True})
    reg = ComponentRegistry(sc)
    sess = Session(sc, reg)
    return sess


def test_proxy_returns_placeholder_when_stubbed():
    from actions.mcp_demo_action import McpDemoAction
    from actions.mcp_proxy_tool_action import McpProxyToolAction

    sess = _make_session()
    # Load demo to ensure server exists
    McpDemoAction(sess).run()

    # Call proxy; since client.call_tool is stubbed, we expect a placeholder content
    action = McpProxyToolAction(sess)
    action.run({'server': 'testmcp', 'tool': 'echo.say', 'args': json.dumps({'text': 'hi'})})

    # Verify assistant context was added
    ctxs = sess.get_contexts('assistant')
    assert ctxs and any('mcp:testmcp/echo.say' in ((c.get() or {}).get('name') or '') for c in ctxs)
