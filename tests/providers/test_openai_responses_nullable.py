import types


class FakeSession:
    def __init__(self, params=None):
        self._params = params or {}

    def get_params(self):
        return self._params

    def get_action(self, name):
        if name == 'assistant_commands':
            return types.SimpleNamespace(get_tool_specs=self._get_tool_specs)
        return None

    def get_effective_tool_mode(self):
        return 'official'

    def _get_tool_specs(self):
        # Canonical: 'a' is required; 'b' optional
        return [{
            'name': 'demo',
            'description': 'Demo tool',
            'parameters': {
                'type': 'object',
                'properties': {
                    'a': {'type': 'string'},
                    'b': {'type': 'integer'}
                },
                'required': ['a'],
                'additionalProperties': True
            }
        }]


def load_provider_module():
    import importlib.util, os, sys
    here = os.path.dirname(__file__)
    root = os.path.abspath(os.path.join(here, '..', '..'))
    mod_path = os.path.join(root, 'providers', 'openairesponses_provider.py')
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location('openairesponses_provider', mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def test_nullable_optionals_adds_null_to_optional_props(monkeypatch):
    mod = load_provider_module()

    # Avoid creating a real OpenAI client by monkeypatching
    class DummyOpenAI:
        def __init__(self, **options):
            self.options = options
    monkeypatch.setattr(mod, 'OpenAI', DummyOpenAI)

    sess = FakeSession({'provider': 'OpenAIResponses', 'api_key': 'x', 'nullable_optionals': True})
    prov = mod.OpenAIResponsesProvider(sess)
    tools = prov.get_tools_for_request()
    assert tools and tools[0]['type'] == 'function'
    params = tools[0]['parameters']
    props = params.get('properties')
    # 'a' required -> should remain simple type (no anyOf null)
    assert isinstance(props['a'], dict) and 'anyOf' not in props['a']
    # 'b' optional -> should include nullable path
    b = props['b']
    assert isinstance(b, dict) and ('anyOf' in b or (isinstance(b.get('type'), list) and 'null' in b['type']))
    if 'anyOf' in b:
        assert any(isinstance(it, dict) and it.get('type') == 'null' for it in b['anyOf'])

