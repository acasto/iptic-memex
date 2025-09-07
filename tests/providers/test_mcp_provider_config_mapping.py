import types
import os, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load_module(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def test_openai_responses_mcp_builtins_mapping(monkeypatch):
    # Fake OpenAI Responses client captures create(**kwargs)
    class FakeResponse:
        def __init__(self, output_text="ok"):
            self.output_text = output_text
            self.usage = types.SimpleNamespace(input_tokens=0, output_tokens=0)
            self.id = "resp_mcp"
            self.output = []

    class FakeResponsesClient:
        def __init__(self):
            self.last_params = None

        def create(self, **kwargs):
            self.last_params = kwargs
            return FakeResponse("ok")

    class FakeOpenAI:
        def __init__(self, **options):
            self.options = options
            self.responses = FakeResponsesClient()

    # Minimal session with MCP active and per-server settings
    class FakeSession:
        def __init__(self):
            self._params = {
                'provider': 'OpenAIResponses',
                'api_key': 'x',
                'model_name': 'gpt-5',
                'mcp_servers': 'c7=https://c7/sse, files=https://files/sse',
                'mcp_headers_c7': '{"Authorization": "Bearer TOKEN"}',
                'mcp_allowed_c7': 'resolve-library-id',
                'mcp_require_approval': 'never',
                'mcp_connector_id_c7': 'conn_123',
                'mcp_description_c7': 'Context7',
            }

        def get_params(self):
            return self._params

        def get_option(self, section, option, fallback=None):
            if section == 'MCP' and option == 'active':
                return True
            return fallback

        def get_action(self, name):
            # Provide empty canonical specs so provider still builds tools list
            if name == 'assistant_commands':
                return types.SimpleNamespace(get_tool_specs=lambda: [])
            return None

        def get_effective_tool_mode(self):
            return 'official'

        def get_context(self, name):
            return None

    mod = load_module(os.path.join(ROOT, 'providers', 'openairesponses_provider.py'), 'openairesponses_provider')
    monkeypatch.setattr(mod, 'OpenAI', FakeOpenAI)

    sess = FakeSession()
    prov = mod.OpenAIResponsesProvider(sess)
    _ = prov.chat()

    tools = prov._client.responses.last_params.get('tools')
    assert isinstance(tools, list) and tools
    # Extract MCP built-ins
    mcp_entries = [t for t in tools if isinstance(t, dict) and t.get('type') == 'mcp']
    # Expect two entries: c7 and files
    labels = {e.get('server_label') for e in mcp_entries}
    assert 'c7' in labels and 'files' in labels
    c7 = next(e for e in mcp_entries if e.get('server_label') == 'c7')
    assert c7.get('server_url') == 'https://c7/sse'
    # Authorization strips 'Bearer '
    assert c7.get('authorization') == 'TOKEN'
    assert c7.get('allowed_tools') == ['resolve-library-id']
    assert c7.get('require_approval') == 'never'
    assert c7.get('connector_id') == 'conn_123'
    assert c7.get('server_description') == 'Context7'


def test_anthropic_mcp_servers_mapping(monkeypatch):
    # Fake Anthropic client captures messages.create / beta.messages.create params
    class FakeMsgResp:
        def __init__(self):
            self.content = [types.SimpleNamespace(text='ok')]
            self.usage = types.SimpleNamespace(input_tokens=0, output_tokens=0)

    class FakeMessages:
        def __init__(self):
            self.last_params = None

        def create(self, **kwargs):
            self.last_params = kwargs
            return FakeMsgResp()

    class FakeBeta:
        def __init__(self):
            self.messages = FakeMessages()

    class FakeAnthropic:
        def __init__(self, **options):
            self.messages = FakeMessages()
            self.beta = FakeBeta()

    class FakeContext:
        def __init__(self, data):
            self._data = data
        def get(self):
            return self._data

    class FakeSession:
        def __init__(self):
            self._params = {
                'provider': 'Anthropic',
                'api_key': 'x',
                'model_name': 'claude-3',
                # Same per-server keys used by OpenAIResponses, provider maps them differently
                'mcp_servers': 'c7=https://c7/sse, files=https://files/sse',
                'mcp_headers_c7': '{"Authorization": "Bearer TOKEN"}',
                'mcp_allowed_c7': 'resolve-library-id',
            }
            self._prompt = FakeContext({'content': 'hi'})
            self._chat = FakeContext([{'role': 'user', 'message': 'hello'}])

        def get_params(self):
            return self._params

        def get_option(self, section, option, fallback=None):
            if section == 'MCP' and option == 'active':
                return True
            return fallback

        def get_action(self, name):
            return None

        def get_context(self, name):
            if name == 'prompt':
                return self._prompt
            if name == 'chat':
                return self._chat
            return None

    mod = load_module(os.path.join(ROOT, 'providers', 'anthropic_provider.py'), 'anthropic_provider')
    monkeypatch.setattr(mod, 'Anthropic', FakeAnthropic)

    sess = FakeSession()
    prov = mod.AnthropicProvider(sess)
    _ = prov.chat()

    # When MCP is present, provider should call beta.messages.create; inspect beta params first
    beta_params = prov.client.beta.messages.last_params
    params = beta_params or prov.client.messages.last_params
    assert isinstance(params, dict)
    servers = params.get('mcp_servers')
    assert isinstance(servers, list) and servers
    # Find c7 entry
    c7 = next((s for s in servers if s.get('name') == 'c7'), None)
    assert c7 is not None
    assert c7.get('type') == 'url'
    assert c7.get('url') == 'https://c7/sse'
    # Authorization token: Bearer stripped
    assert c7.get('authorization_token') == 'TOKEN'
    # Allowed tools mapping
    tc = c7.get('tool_configuration') or {}
    assert tc.get('enabled') is True
    assert tc.get('allowed_tools') == ['resolve-library-id']
