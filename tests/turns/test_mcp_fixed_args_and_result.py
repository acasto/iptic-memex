import os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.turns import TurnRunner, TurnOptions
from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session


class FakeProvider:
    def __init__(self, calls):
        self._calls = list(calls)

    def get_tool_calls(self):
        # Return once; subsequent calls empty
        if self._calls:
            calls = self._calls
            self._calls = []
            return calls
        return []

    def chat(self):
        return ''

    def stream_chat(self):
        yield from []

    def get_messages(self):
        return []

    def get_full_response(self):
        return None


def _make_session() -> Session:
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    # Normalize tools config for test
    sc.set_option('active_tools', '')
    sc.set_option('inactive_tools', '')
    sc.overrides['MCP'] = type('o', (), {'active': True})
    sess = Session(sc, ComponentRegistry(sc))
    return sess


def test_turnrunner_merges_fixed_args_and_formats_result_text():
    from actions.mcp_demo_action import McpDemoAction
    from actions.mcp_register_tools_action import McpRegisterToolsAction

    sess = _make_session()
    # Load demo and register with aliases
    McpDemoAction(sess).run()
    McpRegisterToolsAction(sess).run(['testmcp', '--alias'])

    # Simulate a provider tool call using the alias name; TurnRunner should merge fixed_args
    provider = FakeProvider([
        {'id': 't1', 'name': 'echo.say', 'arguments': {'text': 'hi'}}
    ])
    # Attach to session
    sess.provider = provider

    runner = TurnRunner(sess)
    res = runner.run_user_turn("trigger", options=TurnOptions(stream=False))

    # Chat should now contain a tool role message with 'hi'
    chat = sess.get_context('chat')
    turns = chat.get('all') if chat else []
    assert any(t.get('role') == 'tool' and 'hi' in (t.get('message') or '') for t in turns)

