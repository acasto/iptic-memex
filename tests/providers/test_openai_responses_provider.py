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
    def __init__(self, input_tokens=0, output_tokens=0, total_tokens=0, reasoning_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens
        self.output_tokens_details = {'reasoning_tokens': reasoning_tokens}


class FakeResponse:
    def __init__(self, output_text="", usage=None, output=None):
        self.output_text = output_text
        self.usage = usage or FakeUsage()
        self.id = "resp_123"
        self.output = output or []


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

def test_tools_mapping_and_tool_call_parsing(monkeypatch):
    # Fake assistant_commands to expose canonical specs
    class FakeAssistantCommands:
        def get_tool_specs(self):
            return [{
                'name': 'get_weather',
                'description': 'Get weather',
                'parameters': {
                    'type': 'object',
                    'properties': {'city': {'type': 'string'}},
                    'required': ['city'],
                    'additionalProperties': True  # provider must override to False
                }
            }]

    class SessionWithTools(FakeSession):
        def get_action(self, name):
            if name == 'assistant_commands':
                return FakeAssistantCommands()
            return None
        def get_effective_tool_mode(self):
            return 'official'

    # Fake client returns a response with a function_call output
    class ToolFakeResponsesClient(FakeResponsesClient):
        def create(self, **kwargs):
            self.last_params = kwargs
            class Item:
                def __init__(self):
                    self.type = 'function_call'
                    self.name = 'get_weather'
                    self.arguments = '{"city":"Boston"}'
                    self.id = 'fc_1'
            return FakeResponse(output_text="", output=[Item()])

    class ToolFakeOpenAI(FakeOpenAI):
        def __init__(self, **options):
            super().__init__(**options)
            self.responses = ToolFakeResponsesClient()

    mod = load_provider_module()
    monkeypatch.setattr(mod, 'OpenAI', ToolFakeOpenAI)

    session = SessionWithTools(
        prompt_text=None,
        chat_turns=[{'role': 'user', 'message': 'Q'}],
        params={'provider': 'OpenAIResponses', 'api_key': 'x', 'model_name': 'gpt-5'},
    )
    prov = mod.OpenAIResponsesProvider(session)
    _ = prov.chat()
    # Tools sent
    sent = prov._client.responses.last_params
    assert 'tools' in sent and isinstance(sent['tools'], list) and sent['tools'][0]['type'] == 'function'
    assert sent['tools'][0]['parameters'].get('additionalProperties') is False
    props = sent['tools'][0]['parameters'].get('properties')
    req = sent['tools'][0]['parameters'].get('required')
    assert isinstance(props, dict) and set(req) == set(props.keys())
    # Tool calls parsed
    calls = prov.get_tool_calls()
    assert calls and calls[0]['name'] == 'get_weather' and calls[0]['arguments'].get('city') == 'Boston'
    # Subsequent read should be empty (cleared after read)
    calls2 = prov.get_tool_calls()
    assert calls2 == []

def test_tool_result_turn_maps_to_function_call_output(monkeypatch):
    mod = load_provider_module()
    monkeypatch.setattr(mod, 'OpenAI', FakeOpenAI)

    # Capture last input sent
    class CaptureClient(FakeResponsesClient):
        def create(self, **kwargs):
            self.last_params = kwargs
            return FakeResponse(output_text="done")

    class CaptureOpenAI(FakeOpenAI):
        def __init__(self, **options):
            super().__init__(**options)
            self.responses = CaptureClient()

    monkeypatch.setattr(mod, 'OpenAI', CaptureOpenAI)

    # Chat with a tool result turn
    session = FakeSession(
        prompt_text=None,
        chat_turns=[
            {'role': 'user', 'message': 'calc'},
            {'role': 'tool', 'message': '42', 'tool_call_id': 'fc_123'},
        ],
        params={'provider': 'OpenAIResponses', 'api_key': 'x', 'model_name': 'gpt-5'},
    )
    prov = mod.OpenAIResponsesProvider(session)
    _ = prov.chat()
    sent = prov._client.responses.last_params
    items = sent.get('input')
    assert any(isinstance(it, dict) and it.get('type') == 'function_call_output' and it.get('call_id') == 'fc_123' for it in items)

def test_includes_function_call_and_output_pair_in_same_request(monkeypatch):
    mod = load_provider_module()
    class CaptureClient(FakeResponsesClient):
        def create(self, **kwargs):
            self.last_params = kwargs
            return FakeResponse(output_text="ok")
    class CaptureOpenAI(FakeOpenAI):
        def __init__(self, **options):
            super().__init__(**options)
            self.responses = CaptureClient()
    monkeypatch.setattr(mod, 'OpenAI', CaptureOpenAI)

    # Simulate a chat where the assistant emitted a tool call (captured in chat),
    # and we now include the tool result turn.
    session = FakeSession(
        prompt_text=None,
        chat_turns=[
            {'role': 'user', 'message': 'do math'},
            {'role': 'assistant', 'message': '', 'tool_calls': [
                {'id': 'fc_1', 'name': 'math', 'arguments': {'expression': '1+2'}}
            ]},
            {'role': 'tool', 'message': '3', 'tool_call_id': 'fc_1'},
        ],
        params={'provider': 'OpenAIResponses', 'api_key': 'x', 'model_name': 'gpt-5'},
    )
    prov = mod.OpenAIResponsesProvider(session)
    _ = prov.chat()
    items = prov._client.responses.last_params.get('input')
    # Must include both a function_call and a function_call_output with matching call_id
    types = [it.get('type') for it in items if isinstance(it, dict)]
    assert 'function_call' in types and 'function_call_output' in types
    fc = next(it for it in items if isinstance(it, dict) and it.get('type') == 'function_call')
    fo = next(it for it in items if isinstance(it, dict) and it.get('type') == 'function_call_output')
    assert fc.get('call_id') == fo.get('call_id') == 'fc_1'

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

def test_usage_and_cost_calculation_nonstream(monkeypatch):
    # Fake client produces usage with input/output tokens and reasoning details
    class CostResponsesClient(FakeResponsesClient):
        def create(self, **kwargs):
            self.last_params = kwargs
            usage = FakeUsage(input_tokens=1000, output_tokens=500, total_tokens=1500, reasoning_tokens=100)
            return FakeResponse(output_text="ok", usage=usage)

    class CostOpenAI(FakeOpenAI):
        def __init__(self, **options):
            super().__init__(**options)
            self.responses = CostResponsesClient()

    mod = load_provider_module()
    monkeypatch.setattr(mod, 'OpenAI', CostOpenAI)

    session = FakeSession(
        prompt_text=None,
        chat_turns=[{'role': 'user', 'message': 'hi'}],
        params={
            'provider': 'OpenAIResponses', 'api_key': 'x', 'model_name': 'gpt-5',
            'price_unit': 1000000, 'price_in': 2.0, 'price_out': 10.0,
            'bill_reasoning_as_output': True,
        },
    )
    prov = mod.OpenAIResponsesProvider(session)
    _ = prov.chat()
    usage = prov.get_usage()
    assert usage['total_in'] == 1000 and usage['total_out'] == 500 and usage.get('total_reasoning') == 100
    cost = prov.get_cost()
    # input: 1000/1e6*2 = 0.002; output: (500+100)/1e6*10 = 0.006; total=0.008
    assert round(cost['input_cost'], 6) == 0.002
    assert round(cost['output_cost'], 6) == 0.006
    assert round(cost['total_cost'], 6) == 0.008

def test_streaming_captures_response_id_and_uses_previous_id(monkeypatch):
    # Fake streaming event iterator
    class FakeEvent:
        def __init__(self, typ, resp_id=None):
            self.type = typ
            class R:
                def __init__(self, id):
                    self.id = id
                    self.output = []
            self.response = R(resp_id) if resp_id else None
            self.usage = None

    class StreamResponsesClient(FakeResponsesClient):
        def create(self, **kwargs):
            self.last_params = kwargs
            return iter([FakeEvent('response.created', 'resp_stream_1'), FakeEvent('response.completed', 'resp_stream_1')])

    class StreamOpenAI(FakeOpenAI):
        def __init__(self, **options):
            super().__init__(**options)
            self.responses = StreamResponsesClient()

    mod = load_provider_module()
    monkeypatch.setattr(mod, 'OpenAI', StreamOpenAI)

    # First streaming call: should capture response id
    session = FakeSession(
        prompt_text=None,
        chat_turns=[{'role': 'user', 'message': 'hello'}],
        params={'provider': 'OpenAIResponses', 'api_key': 'x', 'model_name': 'gpt-5', 'stream': True},
    )
    prov = mod.OpenAIResponsesProvider(session)
    chunks = list(prov.stream_chat())
    assert prov._last_response_id == 'resp_stream_1'

    # Next call with store/use_previous_response should include previous_response_id
    session._params['store'] = True
    session._params['use_previous_response'] = True
    _ = prov.chat()
    assert prov._client.responses.last_params.get('previous_response_id') == 'resp_stream_1'

def test_chain_minimize_input_window(monkeypatch):
    mod = load_provider_module()
    class CaptureClient(FakeResponsesClient):
        def create(self, **kwargs):
            self.last_params = kwargs
            return FakeResponse(output_text="ok")
    class CaptureOpenAI(FakeOpenAI):
        def __init__(self, **options):
            super().__init__(**options)
            self.responses = CaptureClient()
    monkeypatch.setattr(mod, 'OpenAI', CaptureOpenAI)

    # Simulate chaining enabled with previous_response_id present
    session = FakeSession(
        prompt_text=None,
        chat_turns=[
            {'role': 'user', 'message': 'u1'},
            {'role': 'assistant', 'message': '', 'tool_calls': [
                {'id': 'fc_x', 'name': 'math', 'arguments': {'expression': '2+2'}}
            ]},
            {'role': 'tool', 'message': '4', 'tool_call_id': 'fc_x'},
        ],
        params={'provider': 'OpenAIResponses', 'api_key': 'x', 'model_name': 'gpt-5', 'store': True, 'use_previous_response': True, 'chain_minimize_input': True},
    )
    prov = mod.OpenAIResponsesProvider(session)
    prov._last_response_id = 'resp_prev'
    _ = prov.chat()
    items = prov._client.responses.last_params.get('input')
    # With chain_minimize_input, we should only include function_call + function_call_output, not the earlier user
    types = [it.get('type') for it in items if isinstance(it, dict)]
    assert 'function_call' in types and 'function_call_output' in types
    assert all(t != 'message' for t in types)
