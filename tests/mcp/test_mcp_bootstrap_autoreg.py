import os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_manager import ConfigManager
from core.session_builder import SessionBuilder


def _make_base_session():
    cfg = ConfigManager()
    builder = SessionBuilder(cfg)
    return cfg, builder


def test_autoreg_http_with_allowed_tools_and_alias(monkeypatch):
    from memex_mcp.client import MCPToolSpec

    class FakeMCPClient:
        def __init__(self):
            self._servers = {}
            self.use_sdk = False
            self.debug = False
            self.http_fallback = False

        def _sdk_enabled(self):
            return False

        def connect_http(self, name, url, headers=None):
            self._servers[name] = {'transport': 'http', 'url': url, 'tools': []}

        def connect_stdio(self, name, cmd):
            self._servers[name] = {'transport': 'stdio', 'cmd': cmd, 'tools': []}

        def list_tools(self, server=None):
            # Return a stable set of tools for the named server
            if server:
                tools = [
                    MCPToolSpec(name='foo', description='F', input_schema={'type': 'object', 'properties': {}}),
                    MCPToolSpec(name='bar', description='B', input_schema={'type': 'object', 'properties': {}}),
                    MCPToolSpec(name='baz', description='Z', input_schema={'type': 'object', 'properties': {}}),
                ]
                return {server: tools}
            return {}

    # Monkeypatch the MCP client factory
    import memex_mcp.client as mclient
    fake_client = FakeMCPClient()
    monkeypatch.setattr(mclient, 'get_or_create_client', lambda session: fake_client)

    cfg, builder = _make_base_session()
    # Create a session config and inject [MCP] sections directly into base_config
    sc = cfg.create_session_config()
    base = sc.base_config
    if not base.has_section('MCP'):
        base.add_section('MCP')
    base.set('MCP', 'active', 'true')
    base.set('MCP', 'mcp_servers', 'shttp')
    base.set('MCP', 'autoload', 'shttp')
    base.set('MCP', 'auto_alias', 'true')

    sec_name = 'MCP.shttp'
    if not base.has_section(sec_name):
        base.add_section(sec_name)
    base.set(sec_name, 'transport', 'http')
    base.set(sec_name, 'url', 'https://demo.local')
    # Limit to two tools (filters auto-registration)
    base.set(sec_name, 'allowed_tools', 'foo,bar')

    # Build session (triggers MCP autoload + auto-register)
    sess = builder.build(mode='chat')

    # Verify assistant-visible tools contain only allowed ones for this server
    from actions.assistant_commands_action import AssistantCommandsAction
    act = AssistantCommandsAction(sess)
    specs = act.get_tool_specs()
    # Aliases are preferred when API-safe; ensure only allowed ones are exposed
    canon = {s.get('canonical_name') for s in specs}
    assert 'foo' in canon
    assert 'bar' in canon
    assert 'baz' not in canon

    # Summary flags recorded
    auto = sess.get_user_data('__mcp_autoload__') or {}
    assert auto.get('shttp') == {'autoload': True, 'alias': True}


def test_autoreg_stdio_with_allowed_tools_and_alias(monkeypatch):
    from memex_mcp.client import MCPToolSpec

    class FakeMCPClient:
        def __init__(self):
            self._servers = {}
            self.use_sdk = False
            self.debug = False
            self.http_fallback = False

        def _sdk_enabled(self):
            return False

        def connect_http(self, name, url, headers=None):
            self._servers[name] = {'transport': 'http', 'url': url, 'tools': []}

        def connect_stdio(self, name, cmd):
            self._servers[name] = {'transport': 'stdio', 'cmd': cmd, 'tools': []}

        def list_tools(self, server=None):
            if server:
                tools = [
                    MCPToolSpec(name='alpha', description='A', input_schema={'type': 'object', 'properties': {}}),
                    MCPToolSpec(name='beta', description='B', input_schema={'type': 'object', 'properties': {}}),
                    MCPToolSpec(name='gamma', description='G', input_schema={'type': 'object', 'properties': {}}),
                ]
                return {server: tools}
            return {}

    import memex_mcp.client as mclient
    fake_client = FakeMCPClient()
    monkeypatch.setattr(mclient, 'get_or_create_client', lambda session: fake_client)

    cfg, builder = _make_base_session()
    sc = cfg.create_session_config()
    base = sc.base_config
    if not base.has_section('MCP'):
        base.add_section('MCP')
    base.set('MCP', 'active', 'true')
    base.set('MCP', 'mcp_servers', 'sio')
    base.set('MCP', 'autoload', 'sio')
    base.set('MCP', 'auto_alias', 'true')

    sec_name = 'MCP.sio'
    if not base.has_section(sec_name):
        base.add_section(sec_name)
    base.set(sec_name, 'transport', 'stdio')
    base.set(sec_name, 'command', 'echo hello')
    base.set(sec_name, 'allowed_tools', 'alpha,beta')

    sess = builder.build(mode='chat')

    from actions.assistant_commands_action import AssistantCommandsAction
    act = AssistantCommandsAction(sess)
    specs = act.get_tool_specs()
    names = {s.get('canonical_name') for s in specs}
    assert 'alpha' in names
    assert 'beta' in names
    assert 'gamma' not in names

    auto = sess.get_user_data('__mcp_autoload__') or {}
    assert auto.get('sio') == {'autoload': True, 'alias': True}
