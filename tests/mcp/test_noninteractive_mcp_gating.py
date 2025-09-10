import types

from config_manager import ConfigManager
from session import SessionBuilder


class DummyClient:
    def __init__(self):
        self.http = []
        self.stdio = []

    def connect_http(self, name, url, headers=None):
        self.http.append((name, url))

    def connect_stdio(self, name, cmd):
        self.stdio.append((name, cmd))


def _enable_demo_mcp(config: ConfigManager) -> None:
    base = config.base_config
    if not base.has_section('MCP'):
        base.add_section('MCP')
    base.set('MCP', 'active', 'true')
    base.set('MCP', 'mcp_servers', 'test')
    base.set('MCP', 'autoload', 'test')
    # Define a demo HTTP server (uses demo.local path handled by the client)
    if not base.has_section('MCP.test'):
        base.add_section('MCP.test')
    base.set('MCP.test', 'transport', 'http')
    base.set('MCP.test', 'url', 'https://demo.local/mcp')
    base.set('MCP.test', 'autoload', 'true')


def _use_mock_model(config: ConfigManager) -> None:
    models = config.models
    if not models.has_section('mock'):
        models.add_section('mock')
    models.set('mock', 'provider', 'Mock')


def test_agent_mcp_gating(monkeypatch):
    cfg = ConfigManager()
    _enable_demo_mcp(cfg)
    _use_mock_model(cfg)

    builder = SessionBuilder(cfg)

    # Patch get_or_create_client to observe connections
    dummy = DummyClient()

    import memex_mcp.client as mcp_client

    def _fake_get_or_create(session):
        # Store the dummy on the session to simulate caching
        session.set_user_data('__mcp_client__', dummy)
        return dummy

    monkeypatch.setattr(mcp_client, 'get_or_create_client', _fake_get_or_create, raising=True)

    from memex_mcp.bootstrap import autoload_mcp

    # Case 1: use_mcp is False (default) → no connections
    sess1 = builder.build(mode='internal', model='mock')
    sess1.enter_agent_mode('deny')
    autoload_mcp(sess1)
    assert dummy.http == [] and dummy.stdio == []

    # Case 2: use_mcp is True → autoload connects to demo server
    dummy.http.clear(); dummy.stdio.clear()
    sess2 = builder.build(mode='internal', model='mock')
    sess2.enter_agent_mode('deny')
    sess2.config.set_option('use_mcp', True)
    autoload_mcp(sess2)
    assert ('test', 'https://demo.local/mcp') in dummy.http
