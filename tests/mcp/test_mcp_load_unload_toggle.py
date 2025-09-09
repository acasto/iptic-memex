import os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_manager import ConfigManager
from core.session_builder import SessionBuilder


def _make_builder():
    cfg = ConfigManager()
    return cfg, SessionBuilder(cfg)


def test_mcp_load_and_unload(monkeypatch):
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

        def disconnect(self, name):
            self._servers.pop(name, None)

        def list_servers(self):
            from memex_mcp.client import MCPServerConnection
            out = []
            for name, s in self._servers.items():
                out.append(MCPServerConnection(name=name, transport=s.get('transport'), url=s.get('url')))
            return out

        def list_tools(self, server=None):
            if server:
                tools = [MCPToolSpec(name='t1'), MCPToolSpec(name='t2')]
                return {server: tools}
            return {}

    import memex_mcp.client as mclient
    fake_client = FakeMCPClient()
    monkeypatch.setattr(mclient, 'get_or_create_client', lambda session: fake_client)

    cfg, builder = _make_builder()
    sc = cfg.create_session_config()
    base = sc.base_config
    if not base.has_section('MCP'):
        base.add_section('MCP')
    base.set('MCP', 'active', 'true')
    base.set('MCP', 'mcp_servers', 'one')
    base.set('MCP', 'auto_alias', 'true')
    # Do not autoload; we'll use /mcp load
    if not base.has_section('MCP.one'):
        base.add_section('MCP.one')
    base.set('MCP.one', 'transport', 'http')
    base.set('MCP.one', 'url', 'https://demo.local')

    sess = builder.build(mode='chat')

    # Initially no dynamic tools
    dyn = sess.get_user_data('__dynamic_tools__') or {}
    assert not dyn

    # Load
    from actions.mcp_load_action import McpLoadAction
    McpLoadAction(sess).run(['one'])
    dyn = sess.get_user_data('__dynamic_tools__') or {}
    assert any(k.startswith('mcp:one/') for k in dyn.keys())

    # Unload
    from actions.mcp_unload_action import McpUnloadAction
    McpUnloadAction(sess).run(['one'])
    dyn = sess.get_user_data('__dynamic_tools__') or {}
    assert not any(k.startswith('mcp:one/') for k in dyn.keys())


def test_set_mcp_on_off_triggers_bootstrap(monkeypatch):
    from memex_mcp.client import MCPToolSpec

    class FakeMCPClient:
        def __init__(self):
            self._servers = {}
            self.use_sdk = False
            self.debug = False
            self.http_fallback = False

        def _sdk_enabled(self):
            return False

        def connect_stdio(self, name, cmd):
            self._servers[name] = {'transport': 'stdio', 'cmd': cmd, 'tools': []}

        def disconnect(self, name):
            self._servers.pop(name, None)

        def list_servers(self):
            from memex_mcp.client import MCPServerConnection
            out = []
            for name, s in self._servers.items():
                out.append(MCPServerConnection(name=name, transport=s.get('transport'), cmd=s.get('cmd')))
            return out

        def list_tools(self, server=None):
            if server:
                tools = [MCPToolSpec(name='echo'), MCPToolSpec(name='sum')]
                return {server: tools}
            return {}

    import memex_mcp.client as mclient
    fake_client = FakeMCPClient()
    monkeypatch.setattr(mclient, 'get_or_create_client', lambda session: fake_client)

    cfg, builder = _make_builder()
    sc = cfg.create_session_config()
    base = sc.base_config
    if not base.has_section('MCP'):
        base.add_section('MCP')
    base.set('MCP', 'active', 'false')
    base.set('MCP', 'mcp_servers', 't0')
    base.set('MCP', 'autoload', 't0')
    base.set('MCP', 'auto_alias', 'true')
    if not base.has_section('MCP.t0'):
        base.add_section('MCP.t0')
    base.set('MCP.t0', 'transport', 'stdio')
    base.set('MCP.t0', 'command', 'echo hi')

    sess = builder.build(mode='chat')

    # Initially inactive → no dynamic tools
    dyn = sess.get_user_data('__dynamic_tools__') or {}
    assert not dyn

    from actions.mcp_toggle_action import McpToggleAction
    McpToggleAction(sess).run(['on'])
    dyn = sess.get_user_data('__dynamic_tools__') or {}
    assert dyn  # tools now registered

    McpToggleAction(sess).run(['off'])
    dyn = sess.get_user_data('__dynamic_tools__') or {}
    assert not dyn


def test_idempotent_set_mcp_on_off(monkeypatch):
    from memex_mcp.client import MCPToolSpec

    class FakeMCPClient:
        def __init__(self):
            self._servers = {}
            self.use_sdk = False
            self.debug = False
            self.http_fallback = False

        def _sdk_enabled(self):
            return False

        def connect_stdio(self, name, cmd):
            self._servers[name] = {'transport': 'stdio', 'cmd': cmd, 'tools': []}

        def disconnect(self, name):
            self._servers.pop(name, None)

        def list_servers(self):
            from memex_mcp.client import MCPServerConnection
            out = []
            for name, s in self._servers.items():
                out.append(MCPServerConnection(name=name, transport=s.get('transport'), cmd=s.get('cmd')))
            return out

        def list_tools(self, server=None):
            if server:
                tools = [MCPToolSpec(name='ping')]
                return {server: tools}
            return {}

    import memex_mcp.client as mclient
    fake_client = FakeMCPClient()
    monkeypatch.setattr(mclient, 'get_or_create_client', lambda session: fake_client)

    cfg, builder = _make_builder()
    sc = cfg.create_session_config()
    base = sc.base_config
    if not base.has_section('MCP'):
        base.add_section('MCP')
    base.set('MCP', 'active', 'true')
    base.set('MCP', 'mcp_servers', 'srv')
    base.set('MCP', 'autoload', 'srv')
    base.set('MCP', 'auto_alias', 'true')
    if not base.has_section('MCP.srv'):
        base.add_section('MCP.srv')
    base.set('MCP.srv', 'transport', 'stdio')
    base.set('MCP.srv', 'command', 'echo hi')

    sess = builder.build(mode='chat')

    # Already active; capture initial dynamic tool count
    dyn = sess.get_user_data('__dynamic_tools__') or {}
    initial_count = len(dyn)

    # Calling 'on' again should be a no-op and not change tool count
    from actions.mcp_toggle_action import McpToggleAction
    McpToggleAction(sess).run(['on'])
    dyn2 = sess.get_user_data('__dynamic_tools__') or {}
    assert len(dyn2) == initial_count

    # Disable
    McpToggleAction(sess).run(['off'])
    dyn3 = sess.get_user_data('__dynamic_tools__') or {}
    assert len(dyn3) == 0

    # Calling 'off' again should remain no-op with empty tools
    McpToggleAction(sess).run(['off'])
    dyn4 = sess.get_user_data('__dynamic_tools__') or {}
    assert len(dyn4) == 0
