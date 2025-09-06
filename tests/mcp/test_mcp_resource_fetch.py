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
    sc.overrides['MCP'] = type('o', (), {'active': True})
    reg = ComponentRegistry(sc)
    sess = Session(sc, reg)
    return sess


def test_fetch_resource_adds_context():
    from actions.mcp_demo_action import McpDemoAction
    from actions.mcp_fetch_resource_action import McpFetchResourceAction

    sess = _make_session()
    McpDemoAction(sess).run()

    act = McpFetchResourceAction(sess)
    act.run(['testmcp', 'guides/welcome'])

    ctxs = sess.get_contexts('mcp_resources')
    assert ctxs and 'demo MCP server' in (ctxs[0].get() or {}).get('content', '')

