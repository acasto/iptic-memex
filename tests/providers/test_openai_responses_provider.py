import types


class FakeContext:
    def __init__(self, data):
        self._data = data

    def get(self, *args, **kwargs):
        return self._data


class FakeSession:
    def __init__(self, prompt_text=None, chat_turns=None, params=None):
        self._prompt = FakeContext({'content': prompt_text}) if prompt_text is not None else None
        self._chat = FakeContext(chat_turns or [])
        self._params = params or {}

    def get_context(self, name):
        if name == 'prompt':
            return self._prompt
        if name == 'chat':
            return self._chat
        return None

    def get_params(self):
        # minimal defaults for provider
        return self._params

    def get_action(self, name):
        return None


class FakeUsage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class FakeResponse:
    def __init__(self, output_text="", usage=None):
        self.output_text = output_text
        self.usage = usage or FakeUsage()
        self.id = "resp_123"


class FakeResponsesClient:
    def __init__(self):
        self.last_params = None

    def create(self, **kwargs):
        self.last_params = kwargs
        # Non-streaming fake response
        return FakeResponse(output_text="ok")


class FakeOpenAI:
    def __init__(self, **options):
        self.options = options
        self.responses = FakeResponsesClient()


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


def test_chat_assembles_input_and_returns_text(monkeypatch):
    mod = load_provider_module()

    monkeypatch.setattr(mod, 'OpenAI', FakeOpenAI)

    session = FakeSession(
        prompt_text="You are helpful",
        chat_turns=[{'role': 'user', 'message': 'Hello?'}],
        params={'provider': 'OpenAIResponses', 'api_key': 'test', 'model_name': 'gpt-5', 'stream': False},
    )

    prov = mod.OpenAIResponsesProvider(session)
    out = prov.chat()

    assert out == "ok"
    # Ensure input was built with the user message
    sent = prov._last_response  # FakeResponse
    # Access last sent params via the fake client
    client = prov._client
    assert isinstance(client, FakeOpenAI)
    assert client.responses.last_params is not None
    inp = client.responses.last_params.get('input')
    assert isinstance(inp, list) and inp[0]['role'] == 'user'
    assert 'Hello?' in inp[0]['content']
    # Ensure token cap maps to max_output_tokens, not max_completion_tokens
    assert 'max_output_tokens' not in client.responses.last_params  # not set in this test


def test_get_messages_returns_list(monkeypatch):
    mod = load_provider_module()
    monkeypatch.setattr(mod, 'OpenAI', FakeOpenAI)

    session = FakeSession(
        prompt_text="Preamble",
        chat_turns=[{'role': 'user', 'message': 'Hi'}, {'role': 'assistant', 'message': 'Hello!'}],
        params={'provider': 'OpenAIResponses', 'api_key': 'test', 'model_name': 'gpt-5'},
    )

    prov = mod.OpenAIResponsesProvider(session)
    msgs = prov.get_messages()
    assert isinstance(msgs, list)
    assert msgs[0]['role'] in ('system', 'developer')
    assert msgs[1]['role'] == 'user'
    assert isinstance(msgs[1]['content'], list)


def test_chat_includes_minimal_input_when_empty(monkeypatch):
    mod = load_provider_module()
    monkeypatch.setattr(mod, 'OpenAI', FakeOpenAI)

    session = FakeSession(
        prompt_text=None,
        chat_turns=[],
        params={'provider': 'OpenAIResponses', 'api_key': 'test', 'model_name': 'gpt-5', 'stream': False},
    )

    prov = mod.OpenAIResponsesProvider(session)
    _ = prov.chat()
    client = prov._client
    inp = client.responses.last_params.get('input')
    assert isinstance(inp, list) and len(inp) == 1
    assert inp[0]['role'] == 'user'
    assert isinstance(inp[0]['content'], str)

def test_maps_max_completion_tokens_to_max_output_tokens(monkeypatch):
    mod = load_provider_module()
    monkeypatch.setattr(mod, 'OpenAI', FakeOpenAI)

    session = FakeSession(
        prompt_text=None,
        chat_turns=[{'role': 'user', 'message': 'Q'}],
        params={'provider': 'OpenAIResponses', 'api_key': 'test', 'model_name': 'gpt-5', 'max_completion_tokens': 123},
    )

    prov = mod.OpenAIResponsesProvider(session)
    _ = prov.chat()
    client = prov._client
    params = client.responses.last_params
    assert params.get('max_output_tokens') == 123
    assert 'max_completion_tokens' not in params

def test_omits_reasoning_effort_and_verbosity(monkeypatch):
    mod = load_provider_module()
    monkeypatch.setattr(mod, 'OpenAI', FakeOpenAI)

    session = FakeSession(
        prompt_text=None,
        chat_turns=[{'role': 'user', 'message': 'Q'}],
        params={
            'provider': 'OpenAIResponses',
            'api_key': 'test',
            'model_name': 'gpt-5',
            'reasoning': True,
            'reasoning_effort': 'minimal',
            'verbosity': 'low',
        },
    )

    prov = mod.OpenAIResponsesProvider(session)
    _ = prov.chat()
    params = prov._client.responses.last_params
    assert 'reasoning_effort' not in params
    assert 'verbosity' not in params

def test_maps_reasoning_effort_into_nested_reasoning(monkeypatch):
    mod = load_provider_module()
    monkeypatch.setattr(mod, 'OpenAI', FakeOpenAI)

    session = FakeSession(
        prompt_text=None,
        chat_turns=[{'role': 'user', 'message': 'Q'}],
        params={
            'provider': 'OpenAIResponses',
            'api_key': 'test',
            'model_name': 'o3-mini',
            'reasoning': True,
            'reasoning_effort': 'High',
        },
    )

    prov = mod.OpenAIResponsesProvider(session)
    _ = prov.chat()
    params = prov._client.responses.last_params
    assert 'reasoning' in params and isinstance(params['reasoning'], dict)
    assert params['reasoning'].get('effort') == 'high'
