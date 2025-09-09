import os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_manager import ConfigManager
from component_registry import ComponentRegistry
from session import Session
from core.turns import TurnRunner


class FakeProvider:
    def __init__(self, calls):
        self._calls = list(calls or [])

    def get_tool_calls(self):
        return list(self._calls)


def _make_session():
    cfg = ConfigManager()
    sc = cfg.create_session_config()
    # Ensure MCP gate is on for dynamic MCP tools
    sc.set_option('active', True)
    sc.overrides['MCP'] = type('o', (), {'active': True})
    sess = Session(sc, ComponentRegistry(sc))
    return sess


def test_turnrunner_maps_api_safe_name_and_executes(monkeypatch):
    sess = _make_session()
    # Register a dynamic tool under canonical name and map API-safe name back to it
    canonical = 'mcp:test/echo.say'
    dyn = {
        canonical: {
            'name': canonical,
            'description': 'Test tool',
            'args': ['text'],
            'required': ['text'],
            'schema': {'properties': {'text': {'type': 'string'}}},
            'auto_submit': True,
            'function': {
                'type': 'action',
                'name': 'mcp_proxy_tool',
                'fixed_args': {'server': 'test', 'tool': 'echo.say'},
            },
        }
    }
    sess.set_user_data('__dynamic_tools__', dyn)
    # API-safe → canonical mapping
    sess.set_user_data('__tool_api_to_cmd__', {'mcp_test_echo_say': canonical})

    # Provider returns a tool_call with the API-safe name and a call_id
    sess.provider = FakeProvider([{'id': 't1', 'name': 'mcp_test_echo_say', 'arguments': {'text': 'hi'}}])

    tr = TurnRunner(sess)
    ran = tr._execute_tools("")
    assert ran is True

    chat = sess.get_context('chat')
    turns = chat.get('all') if chat else []
    # Expect a tool message with the same call_id
    tool_msgs = [t for t in turns if t.get('role') == 'tool']
    assert tool_msgs and tool_msgs[-1].get('tool_call_id') == 't1'


def test_turnrunner_emits_stub_on_unsupported_tool(monkeypatch):
    sess = _make_session()
    # No dynamic tools registered; mapping empty
    sess.set_user_data('__dynamic_tools__', {})
    sess.set_user_data('__tool_api_to_cmd__', {})
    sess.provider = FakeProvider([{'id': 't2', 'name': 'unknown_tool', 'arguments': {}}])

    tr = TurnRunner(sess)
    ran = tr._execute_tools("")
    assert ran is True
    chat = sess.get_context('chat')
    turns = chat.get('all') if chat else []
    tool_msgs = [t for t in turns if t.get('role') == 'tool']
    assert tool_msgs and tool_msgs[-1].get('tool_call_id') == 't2'
    assert 'Unsupported tool call' in tool_msgs[-1].get('message', '')


def test_turnrunner_emits_stub_on_action_exception(monkeypatch):
    sess = _make_session()
    # Dynamic tool that points to a non-existent action to force an exception
    canonical = 'x:do'
    dyn = {
        canonical: {
            'name': canonical,
            'description': 'Broken tool',
            'args': [],
            'required': [],
            'schema': {'properties': {}},
            'auto_submit': True,
            'function': {'type': 'action', 'name': 'nonexistent_action'},
        }
    }
    sess.set_user_data('__dynamic_tools__', dyn)
    sess.set_user_data('__tool_api_to_cmd__', {'x_do': canonical})
    sess.provider = FakeProvider([{'id': 't3', 'name': 'x_do', 'arguments': {}}])

    tr = TurnRunner(sess)
    ran = tr._execute_tools("")
    assert ran is True
    chat = sess.get_context('chat')
    turns = chat.get('all') if chat else []
    tool_msgs = [t for t in turns if t.get('role') == 'tool']
    assert tool_msgs and tool_msgs[-1].get('tool_call_id') == 't3'
    assert 'Error:' in (tool_msgs[-1].get('message') or '')

