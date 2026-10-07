"""Exercise both providers through the real SDK, with no network or API keys."""

import json
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from contexts.chat_context import ChatContext
from core.turns import TurnRunner
from providers.openai_provider import OpenAIProvider
from providers.openairesponses_provider import OpenAIResponsesProvider


class Session:
    def __init__(self, params=None, tools=False):
        self.params = {'provider': 'Test', 'model_name': 'test-model', **(params or {})}
        self.chat = ChatContext(self)
        self.chat.add('Please echo hello')
        self.tools = tools
        self.data = {'__tool_api_to_cmd__': {'echo_api': 'mcp:demo.echo'}}
        self.flags = {}
        self.executed = []
        self.ui = SimpleNamespace(emit=lambda *a, **k: None,
                                  capabilities=SimpleNamespace(blocking=False))
        output = SimpleNamespace(write=lambda *a, **k: None, warning=lambda *a, **k: None,
                                 stop_spinner=lambda: None, spinner=lambda *a, **k: nullcontext())
        self.utils = SimpleNamespace(output=output)
        self.commands = SimpleNamespace(
            commands={'mcp:demo.echo': {'function': {'type': 'action', 'name': 'echo'},
                                      'auto_submit': True}},
            get_tool_specs=lambda: [{
                'name': 'echo_api', 'description': 'Echo a value',
                'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}},
                               'required': ['value']},
            }],
        )

    def get_params(self):
        return self.params

    def get_context(self, name):
        return self.chat if name == 'chat' else None

    def get_contexts(self, kind=None):
        return []

    def get_action(self, name):
        if name == 'assistant_commands':
            return self.commands if self.tools else None
        if name == 'echo':
            return SimpleNamespace(run=lambda args, content: self.executed.append(args))
        return None

    def get_effective_tool_mode(self):
        return 'official' if self.tools else 'none'

    def get_option(self, section, key, fallback=None):
        return fallback

    def get_user_data(self, name, default=None):
        return self.data.get(name, default)

    def get_provider(self):
        return self.provider

    def get_flag(self, name):
        return self.flags.get(name)

    def set_flag(self, name, value):
        self.flags[name] = value

    def get_cancellation_token(self):
        return None


def response(output=None, status='completed', **kwargs):
    return {'id': 'resp_1', 'object': 'response', 'created_at': 1,
            'status': status, 'model': 'test-model',
            'output': output or [], 'usage': None, **kwargs}


def message(text='Done', phase='final_answer'):
    return {'type': 'message', 'id': 'msg_1', 'role': 'assistant', 'status': 'completed',
            'phase': phase,
            'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}


def function_call(arguments='{"value":"hello"}', **kwargs):
    return {'type': 'function_call', 'id': 'fc_item_1', 'call_id': 'call_pair_1',
            'name': 'echo_api', 'arguments': arguments, 'status': 'completed', **kwargs}


def chat_response(**kwargs):
    return {'id': 'chat_1', 'object': 'chat.completion', 'created': 1, 'model': 'test-model',
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': 'Done'}}],
            'usage': None, **kwargs}


def sse(events):
    return ''.join(f'event: {event["type"]}\ndata: {json.dumps(event)}\n\n' for event in events)


def make_provider(monkeypatch, provider_class, session, replies):
    """Capture the exact JSON serialized by the installed OpenAI SDK."""
    requests = []
    iterator = iter(replies)

    def handle(request):
        requests.append(json.loads(request.content))
        reply = next(iterator)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, str):
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=reply)
        return httpx.Response(200, json=reply)

    client = OpenAI(api_key='offline-test', base_url='http://offline.test/v1', max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(provider_class, '_initialize_client', lambda self: client)
    provider = provider_class(session)
    session.provider = provider
    return provider, requests


@pytest.mark.parametrize('store', [False, True])
def test_sdk_tool_round_trip_preserves_outputs_and_call_id(monkeypatch, store):
    session = Session({'store': store, 'use_previous_response': store}, tools=True)
    outputs = [
        {'type': 'reasoning', 'id': 'rs_1', 'summary': [], 'encrypted_content': 'opaque'},
        message('I will echo that.', phase='commentary'), function_call(),
    ]
    provider, requests = make_provider(monkeypatch, OpenAIResponsesProvider, session,
                                       [response(outputs), response([message()])])
    text = provider.chat()
    runner = TurnRunner(session)
    runner._record_assistant(text)
    assert runner._execute_tools(text)
    assert session.executed == [{'value': 'hello'}]
    assistant = session.chat.get()[1]
    assert assistant['message'] == 'I will echo that.'
    assert assistant['tool_calls'][0]['id'] == 'call_pair_1'
    assert assistant['responses_output'] == outputs
    display = provider.get_messages()[1]
    assert display['tool_calls'][0]['function']['name'] == 'echo_api'
    assert display['tool_calls'][0]['id'] == 'call_pair_1'
    assert provider.chat() == 'Done'
    followup = requests[1]
    if store:
        assert followup['previous_response_id'] == 'resp_1'
        assert followup['input'] == [
            {'type': 'function_call_output', 'call_id': 'call_pair_1', 'output': 'OK'},
        ]
    else:
        assert 'previous_response_id' not in followup
        assert followup['input'][1:4] == outputs
        assert followup['input'][4]['call_id'] == 'call_pair_1'
        assert len(followup['input']) == 5
    json.dumps(assistant)  # Transcript metadata must be serializable.


@pytest.mark.parametrize('mutation', ['edit_user', 'edit_assistant', 'trim', 'clear', 'model'])
def test_responses_chaining_rejects_changed_history(monkeypatch, mutation):
    session = Session({'store': True, 'use_previous_response': True})
    provider, requests = make_provider(monkeypatch, OpenAIResponsesProvider, session,
                                       [response([message()]), response([message()])])
    TurnRunner(session)._record_assistant(provider.chat())
    if mutation == 'edit_user':
        session.chat.conversation[0]['message'] = 'Edited question'
    elif mutation == 'edit_assistant':
        session.chat.conversation[1]['message'] = 'Edited answer'
    elif mutation == 'trim':
        session.chat.conversation.pop(0)
    elif mutation == 'clear':
        session.chat.clear()
        provider.reset_usage()
    else:
        session.params['model_name'] = 'different-model'
    session.chat.add('Next question')
    provider.chat()
    assert 'previous_response_id' not in requests[1]
    assert any(item.get('content') == 'Next question' for item in requests[1]['input'])
    if mutation == 'edit_assistant':
        assert any(item.get('content') == 'Edited answer' for item in requests[1]['input'])


def test_responses_chains_all_new_turns_without_duplicate_assistant(monkeypatch):
    session = Session({'store': True, 'use_previous_response': True})
    provider, requests = make_provider(monkeypatch, OpenAIResponsesProvider, session,
                                       [response([message()]), response([message()])])
    TurnRunner(session)._record_assistant(provider.chat())
    session.chat.add('Next question')
    session.chat.add('Extra detail')
    provider.chat()
    assert requests[1]['previous_response_id'] == 'resp_1'
    assert requests[1]['input'] == [
        {'role': 'user', 'content': 'Next question'}, {'role': 'user', 'content': 'Extra detail'},
    ]


@pytest.mark.parametrize('arguments', ['{', '[]', 'null', '', None])
@pytest.mark.parametrize('stream', [False, True])
def test_responses_rejects_invalid_arguments(monkeypatch, arguments, stream):
    session = Session({'stream': stream}, tools=True)
    final = response([function_call(arguments)])
    reply = sse([{'type': 'response.completed', 'response': final}]) if stream else final
    provider, _ = make_provider(monkeypatch, OpenAIResponsesProvider, session, [reply])
    if stream:
        list(provider.stream_chat())
    else:
        provider.chat()
    calls = provider.get_tool_calls()
    assert calls[0]['truncated']
    assert calls[0]['id'] == 'call_pair_1'
    assert provider.get_tool_calls() == []


@pytest.mark.parametrize('status', ['incomplete', 'failed'])
@pytest.mark.parametrize('stream', [False, True])
def test_responses_terminal_errors_keep_usage_and_block_calls(monkeypatch, status, stream):
    session = Session({'stream': stream})
    final = response([message('Partial'), function_call()], status=status,
                     incomplete_details={'reason': 'max_output_tokens'} if status == 'incomplete' else None,
                     error={'code': 'server_error', 'message': 'Oops'} if status == 'failed' else None,
                     usage={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15,
                            'input_tokens_details': {'cached_tokens': 2},
                            'output_tokens_details': {'reasoning_tokens': 3}})
    reply = sse([{'type': 'response.output_text.delta', 'delta': 'Partial'},
                 {'type': f'response.{status}', 'response': final}]) if stream else final
    provider, _ = make_provider(monkeypatch, OpenAIResponsesProvider, session, [reply])
    text = ''.join(provider.stream_chat()) if stream else provider.chat()
    assert 'Partial' in text and ('incomplete' if status == 'incomplete' else 'Oops') in text
    assert provider.get_usage()['turn_out'] == 5
    assert provider.get_usage()['turn_cached'] == 2
    assert provider.get_tool_calls()[0]['truncated']
    assert provider.get_finish_reason() == ('length' if status == 'incomplete' else 'error')
    assert provider.get_full_response().id == 'resp_1'


@pytest.mark.parametrize('stream', [False, True])
def test_responses_refusal_is_visible(monkeypatch, stream):
    session = Session({'stream': stream})
    output = message()
    output['content'] = [{'type': 'refusal', 'refusal': 'Cannot do that'}]
    final = response([output])
    reply = sse([{'type': 'response.completed', 'response': final}]) if stream else final
    provider, _ = make_provider(monkeypatch, OpenAIResponsesProvider, session, [reply])
    assert (''.join(provider.stream_chat()) if stream else provider.chat()) == 'Cannot do that'


def test_responses_eof_without_terminal_is_not_a_tool_result(monkeypatch):
    session = Session({'stream': True, 'store': True})
    reply = sse([{'type': 'response.created', 'response': response(status='in_progress')},
                 {'type': 'response.output_item.added', 'output_index': 0, 'item': function_call()}])
    provider, _ = make_provider(monkeypatch, OpenAIResponsesProvider, session, [reply])
    assert 'Stream interrupted' in ''.join(provider.stream_chat())
    assert provider.get_tool_calls() == []
    assert provider.get_assistant_metadata() == {}
    assert provider._last_response_id is None


def test_responses_native_parameters_and_image_input(monkeypatch):
    session = Session({'vision': True, 'max_output_tokens': 200, 'max_completion_tokens': 999,
                       'temperature': 0.2, 'top_p': 0.8, 'verbosity': 'Low',
                       'reasoning': {'context': 'all_turns'}, 'reasoning_effort': 'High',
                       'text': {'format': {'type': 'json_object'}},
                       'include': ['reasoning.encrypted_content'],
                       'extra_body': {'top_k': 20}, 'excluded_parameters': 'top_p'})
    image = SimpleNamespace(get=lambda: {'mime_type': 'image/png', 'content': 'aW1hZ2U='})
    note = SimpleNamespace(get=lambda: {'content': 'Important note', 'name': 'note'})
    session.chat.conversation[0]['context'] = [
        {'type': 'image', 'context': image}, {'type': 'assistant', 'context': note},
    ]
    original = deepcopy(session.params)
    provider, requests = make_provider(monkeypatch, OpenAIResponsesProvider, session, [response()])
    provider.chat()
    sent = requests[0]
    assert sent['max_output_tokens'] == 200 and 'max_completion_tokens' not in sent
    assert sent['text'] == {'format': {'type': 'json_object'}, 'verbosity': 'low'}
    assert sent['reasoning'] == {'context': 'all_turns', 'effort': 'high'}
    assert sent['temperature'] == 0.2 and 'top_p' not in sent and sent['top_k'] == 20
    assert sent['include'] == ['reasoning.encrypted_content']
    assert sent['input'][0]['content'][1] == {
        'type': 'input_image', 'image_url': 'data:image/png;base64,aW1hZ2U=',
    }
    assert 'Important note' in sent['input'][0]['content'][0]['text']
    assert session.params == original


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
def test_usage_nullable_details_cache_pricing_and_model_switch(monkeypatch, provider_class):
    session = Session({'price_in': 2, 'price_cache_in': 0.5, 'price_out': 10})
    if provider_class is OpenAIProvider:
        replies = [chat_response(usage={'prompt_tokens': 1000, 'completion_tokens': 500,
                                       'total_tokens': 1500,
                                       'prompt_tokens_details': {'cached_tokens': 400},
                                       'completion_tokens_details': {'reasoning_tokens': 100,
                                                                     'accepted_prediction_tokens': None,
                                                                     'rejected_prediction_tokens': None}}),
                   chat_response(usage={'prompt_tokens': 1000, 'completion_tokens': 500,
                                        'total_tokens': 1500,
                                        'prompt_tokens_details': None, 'completion_tokens_details': None})]
    else:
        replies = [response([message()], usage={
            'input_tokens': 1000, 'output_tokens': 500, 'total_tokens': 1500,
            'input_tokens_details': {'cached_tokens': 400},
            'output_tokens_details': {'reasoning_tokens': 100}}), response([message()], usage={
            'input_tokens': 1000, 'output_tokens': 500, 'total_tokens': 1500,
            'input_tokens_details': None, 'output_tokens_details': None})]
    provider, _ = make_provider(monkeypatch, provider_class, session, replies)
    assert provider.chat() == 'Done'
    assert provider.get_cost() == {'input_cost': 0.0014, 'output_cost': 0.005, 'total_cost': 0.0064}
    session.params.update(model_name='expensive-model', price_in=4, price_out=20)
    assert provider.chat() == 'Done'
    assert provider.get_cost() == {'input_cost': 0.0054, 'output_cost': 0.015, 'total_cost': 0.0204}
    snapshot = provider.get_usage()
    provider.reset_usage()
    provider.set_usage(snapshot)
    assert provider.get_usage()['total_tokens'] == 3000
    assert provider.get_cost()['total_cost'] == 0.0204


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
def test_failed_request_clears_turn_usage(monkeypatch, provider_class):
    session = Session()
    if provider_class is OpenAIProvider:
        reply = chat_response(usage={'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15})
    else:
        reply = response(usage={'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15})
    provider, _ = make_provider(monkeypatch, provider_class, session,
                               [reply, httpx.ConnectError('Offline failure')])
    provider.chat()
    assert provider.get_usage()['turn_in'] == 10
    provider.chat()
    assert 'turn_in' not in provider.get_usage()
    assert provider.get_usage()['total_in'] == 10
    assert provider.get_tool_calls() == []


@pytest.mark.parametrize('reasoning_field', ['reasoning', 'reasoning_content'])
@pytest.mark.parametrize('simple', [False, True])
def test_chat_reasoning_and_api_name_survive_tool_round_trip(monkeypatch, reasoning_field, simple):
    session = Session({'reasoning': True, 'reasoning_effort': 'High',
                       'use_old_system_role': True, 'use_simple_message_format': simple}, tools=True)
    reply = chat_response(choices=[{'index': 0, 'finish_reason': 'tool_calls',
        'message': {'role': 'assistant', 'content': 'Calling echo', reasoning_field: 'Thinking',
                    'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {
                        'name': 'echo_api', 'arguments': '{"value":"hello"}'}}]}}])
    provider, requests = make_provider(monkeypatch, OpenAIProvider, session, [reply, chat_response()])
    text = provider.chat()
    runner = TurnRunner(session)
    runner._record_assistant(text)
    assert runner._execute_tools(text)
    assert session.executed == [{'value': 'hello'}]
    provider.chat()
    assistant = requests[1]['messages'][1]
    assert assistant['content'] == 'Calling echo'
    assert assistant[reasoning_field] == 'Thinking'
    assert assistant['tool_calls'][0]['function']['name'] == 'echo_api'
    assert requests[1]['reasoning_effort'] == 'high'


def test_chat_honors_native_parameters_without_reasoning_flag(monkeypatch):
    session = Session({'max_completion_tokens': 123, 'max_tokens': 999,
                       'reasoning_effort': 'Low', 'extra_body': {'top_k': 12, 'temperature': 0.9},
                       'temperature': 0.2, 'excluded_parameters': 'temperature'})
    provider, requests = make_provider(monkeypatch, OpenAIProvider, session, [chat_response()])
    provider.chat()
    assert requests[0]['max_completion_tokens'] == 123
    assert requests[0]['reasoning_effort'] == 'low'
    assert 'max_tokens' not in requests[0] and 'temperature' not in requests[0]
    assert requests[0]['top_k'] == 12


def test_nested_strict_schema_does_not_mutate_canonical_specs(monkeypatch):
    session = Session({'nullable_optionals': True}, tools=True)
    schema = {'type': 'object', 'properties': {
        'nested': {'type': 'object', 'properties': {'needed': {'type': 'string'},
                   'optional': {'anyOf': [{'type': 'number'}]}}, 'required': ['needed']},
        'rows': {'type': 'array', 'items': {'$ref': '#/$defs/row'}},
    }, 'required': ['rows'], '$defs': {
        'row': {'type': 'object', 'properties': {'value': {'type': 'integer'}}},
    }}
    specs = [{'name': 'echo_api', 'parameters': schema}]
    session.commands.get_tool_specs = lambda: specs
    original = deepcopy(specs)
    provider, _ = make_provider(monkeypatch, OpenAIResponsesProvider, session, [])
    sent = provider.get_tools_for_request()[0]['parameters']
    assert specs == original
    assert sent['required'] == ['nested', 'rows'] and sent['additionalProperties'] is False
    nested = sent['properties']['nested']['anyOf'][0]
    assert nested['required'] == ['needed', 'optional'] and nested['additionalProperties'] is False
    assert {'type': 'null'} in nested['properties']['optional']['anyOf']
    assert sent['$defs']['row']['required'] == ['value']
    assert provider.get_tools_for_request()[0]['parameters'] == sent


def chat_chunk(delta=None, finish_reason=None, choices=None, usage=None):
    return {'id': 'chat_1', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test-model',
            'choices': choices if choices is not None else [
                {'index': 0, 'delta': delta or {}, 'finish_reason': finish_reason}],
            'usage': usage}


def chat_sse(chunks):
    return ''.join(f'data: {json.dumps(chunk)}\n\n' for chunk in chunks) + 'data: [DONE]\n\n'


@pytest.mark.parametrize('reasoning_field', ['reasoning', 'reasoning_content'])
def test_chat_sdk_stream_fragments_calls_and_usage(monkeypatch, reasoning_field):
    session = Session({'stream': True, 'price_in': 2, 'price_out': 10}, tools=True)
    chunks = [
        chat_chunk({reasoning_field: 'Thinking ', 'tool_calls': [
            {'index': 0, 'id': 'call_1', 'type': 'function',
             'function': {'name': 'echo_api', 'arguments': '{"value":'}}]}),
        chat_chunk({reasoning_field: 'carefully', 'tool_calls': [
            {'index': 0, 'function': {'arguments': '"hello"}'}}]}),
        chat_chunk({'content': 'Calling echo'}, finish_reason='tool_calls'),
        chat_chunk(choices=[], usage={'prompt_tokens': 10, 'completion_tokens': 5,
                                     'total_tokens': 15, 'prompt_tokens_details': None,
                                     'completion_tokens_details': {'reasoning_tokens': 3}}),
    ]
    provider, requests = make_provider(monkeypatch, OpenAIProvider, session, [chat_sse(chunks)])
    assert ''.join(provider.stream_chat()) == 'Calling echo'
    assert provider.get_current_reasoning() == 'Thinking carefully'
    assert provider.get_assistant_metadata() == {'reasoning_field': reasoning_field}
    call = provider.get_tool_calls()[0]
    assert call == {'id': 'call_1', 'name': 'mcp:demo.echo', 'api_name': 'echo_api',
                    'arguments': {'value': 'hello'}, 'truncated': False}
    assert provider.get_tool_calls() == []
    assert provider.get_usage()['turn_out'] == 5
    assert provider.get_cost()['output_cost'] == 0.00005
    assert requests[0]['stream_options'] == {'include_usage': True}


def test_chat_sdk_stream_ignores_other_choices(monkeypatch):
    session = Session({'stream': True, 'n': 2})
    chunks = [chat_chunk(choices=[
        {'index': 1, 'delta': {'content': 'Wrong answer', 'reasoning': 'Wrong reasoning'},
         'finish_reason': None},
        {'index': 0, 'delta': {'content': 'Right answer'}, 'finish_reason': None},
    ]), chat_chunk(finish_reason='stop')]
    provider, _ = make_provider(monkeypatch, OpenAIProvider, session, [chat_sse(chunks)])
    assert ''.join(provider.stream_chat()) == 'Right answer'
    assert provider.get_current_reasoning() is None


def test_chat_eof_does_not_execute_even_valid_json(monkeypatch):
    session = Session({'stream': True}, tools=True)
    chunks = [chat_chunk({'tool_calls': [
        {'index': 0, 'id': 'call_1', 'type': 'function',
         'function': {'name': 'echo_api', 'arguments': '{"value":"hello"}'}}]})]
    provider, _ = make_provider(monkeypatch, OpenAIProvider, session, [chat_sse(chunks)])
    assert 'no terminal finish reason' in ''.join(provider.stream_chat())
    assert provider.get_tool_calls()[0]['truncated']


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
def test_generator_cancellation_closes_sdk_stream(monkeypatch, provider_class):
    class Body(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            if provider_class is OpenAIProvider:
                yield chat_sse([chat_chunk({'content': 'First'})]).replace('data: [DONE]\n\n', '').encode()
                yield chat_sse([chat_chunk(finish_reason='stop')]).encode()
            else:
                yield sse([{'type': 'response.output_text.delta', 'delta': 'First'}]).encode()
                yield sse([{'type': 'response.completed', 'response': response([message()])}]).encode()

        def close(self):
            self.closed = True

    body = Body()
    client = OpenAI(api_key='offline-test', base_url='http://offline.test/v1',
                    http_client=httpx.Client(transport=httpx.MockTransport(
                        lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                                      stream=body))))
    monkeypatch.setattr(provider_class, '_initialize_client', lambda self: client)
    provider = provider_class(Session({'stream': True}))
    iterator = provider.stream_chat()
    assert next(iterator) == 'First'
    assert not body.closed
    iterator.close()
    assert body.closed
    assert provider.get_tool_calls() == []
    if provider_class is OpenAIResponsesProvider:
        assert provider.get_assistant_metadata() == {}


@pytest.mark.parametrize('auto_approve', [False, True])
@pytest.mark.parametrize('store', [False, True])
def test_mcp_approval_uses_broker_and_preserves_storage_choice(monkeypatch, auto_approve, store):
    session = Session({'store': store, 'use_previous_response': store,
                       'mcp_auto_approve': auto_approve})
    pending = {'type': 'mcp_approval_request', 'id': 'approval_1',
               'server_label': 'demo', 'name': 'echo', 'arguments': '{"value":"hello"}'}
    provider, requests = make_provider(monkeypatch, OpenAIResponsesProvider, session,
                                       [response([pending]), response([message()]), response([message()])])
    prompted = []
    session.data['__interaction_broker__'] = SimpleNamespace(
        prompt=lambda need: prompted.append(need) or False)
    TurnRunner(session)._record_assistant(provider.chat())
    session.chat.add('Continue')
    TurnRunner(session)._record_assistant(provider.chat())
    assert requests[1]['store'] is store
    approval = next(it for it in requests[1]['input'] if it.get('type') == 'mcp_approval_response')
    assert approval == {'type': 'mcp_approval_response', 'approve': auto_approve,
                        'approval_request_id': 'approval_1'}
    assert len(prompted) == (0 if auto_approve else 1)
    if prompted:
        assert 'hello' in prompted[0].spec['prompt']
    session.chat.add('Next')
    provider.chat()
    if store:
        assert requests[2]['previous_response_id'] == 'resp_1'
        assert len(requests[2]['input']) == 1
    else:
        assert approval in requests[2]['input']


def test_mcp_auto_approve_does_not_enable_storage_without_pending_requests(monkeypatch):
    session = Session({'store': False, 'mcp_auto_approve': True})
    provider, requests = make_provider(monkeypatch, OpenAIResponsesProvider, session,
                                       [response([message()]), response([message()])])
    TurnRunner(session)._record_assistant(provider.chat())
    session.chat.add('Next')
    provider.chat()
    assert requests[1]['store'] is False and 'previous_response_id' not in requests[1]


def test_mcp_per_server_approval_policy_wins(monkeypatch):
    session = Session({'mcp_servers': {'demo': 'https://example.test/mcp'},
                       'mcp_require_approval': 'never', 'mcp_require_approval_demo': 'always'})
    provider, _ = make_provider(monkeypatch, OpenAIResponsesProvider, session, [])
    assert provider._build_mcp_tools()[0]['require_approval'] == 'always'


@pytest.mark.parametrize('simple', [False, True])
@pytest.mark.parametrize('old_system', [False, True])
def test_chat_legacy_message_flags_remain_supported(monkeypatch, simple, old_system):
    session = Session({'use_simple_message_format': simple, 'use_old_system_role': old_system})
    prompt = SimpleNamespace(get=lambda: {'content': 'System instructions'})
    session.get_context = lambda name: prompt if name == 'prompt' else session.chat if name == 'chat' else None
    provider, requests = make_provider(monkeypatch, OpenAIProvider, session, [chat_response()])
    provider.chat()
    assert requests[0]['messages'][0] == {
        'role': 'system' if old_system else 'developer', 'content': 'System instructions',
    }
    assert isinstance(requests[0]['messages'][1]['content'], str if simple else list)


def test_chat_stream_assembles_multiple_calls_and_fragmented_names(monkeypatch):
    session = Session({'stream': True}, tools=True)
    chunks = [chat_chunk({'tool_calls': [
        {'index': 1, 'id': 'call_2', 'type': 'function',
         'function': {'name': 'echo_api', 'arguments': '{"value":"two"}'}},
        {'index': 0, 'id': 'call_1', 'type': 'function',
         'function': {'name': 'echo_', 'arguments': '{"value":'}},
    ]}), chat_chunk({'tool_calls': [
        {'index': 0, 'function': {'name': 'api', 'arguments': '"one"}'}},
    ]}, finish_reason='tool_calls')]
    provider, _ = make_provider(monkeypatch, OpenAIProvider, session, [chat_sse(chunks)])
    list(provider.stream_chat())
    calls = provider.get_tool_calls()
    assert [call['id'] for call in calls] == ['call_1', 'call_2']
    assert [call['arguments']['value'] for call in calls] == ['one', 'two']
    assert all(call['name'] == 'mcp:demo.echo' and not call['truncated'] for call in calls)


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
def test_legacy_reasoning_billing_flag_does_not_double_charge(monkeypatch, provider_class):
    session = Session({'price_out': 10, 'bill_reasoning_as_output': False})
    if provider_class is OpenAIProvider:
        reply = chat_response(usage={'prompt_tokens': 0, 'completion_tokens': 500,
                                    'total_tokens': 500,
                                    'completion_tokens_details': {'reasoning_tokens': 100}})
    else:
        reply = response(usage={'input_tokens': 0, 'output_tokens': 500, 'total_tokens': 500,
                                'output_tokens_details': {'reasoning_tokens': 100}})
    provider, _ = make_provider(monkeypatch, provider_class, session, [reply])
    provider.chat()
    assert provider.get_cost()['output_cost'] == 0.005


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
def test_provider_error_never_executes_pseudo_commands(monkeypatch, provider_class):
    session = Session(tools=True)
    session.commands.parse_commands = lambda text: ['command']
    session.commands.run = lambda text: session.executed.append(text)
    provider, _ = make_provider(monkeypatch, provider_class, session, [httpx.ConnectError('Offline')])
    error = provider.chat()
    assert provider.get_finish_reason() == 'error'
    assert not TurnRunner(session)._execute_tools(error)
    assert session.executed == []


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
def test_sdk_client_cleanup_and_clear_response_state(monkeypatch, provider_class):
    session = Session()
    reply = chat_response() if provider_class is OpenAIProvider else response([message()])
    provider, _ = make_provider(monkeypatch, provider_class, session, [reply])
    provider.chat()
    provider.reset_usage()
    assert provider.get_full_response() is None
    assert provider.get_assistant_metadata() == {}
    assert provider.get_finish_reason() is None
    client = provider.client if provider_class is OpenAIProvider else provider._client
    provider.cleanup()
    assert client.is_closed()


def test_responses_exclusions_apply_before_alias_mapping(monkeypatch):
    session = Session({'reasoning_effort': 'high', 'reasoning_summary': 'auto', 'verbosity': 'low',
                       'excluded_parameters': ['reasoning_effort', 'reasoning_summary', 'verbosity']})
    provider, requests = make_provider(monkeypatch, OpenAIResponsesProvider, session, [response()])
    provider.chat()
    assert 'reasoning' not in requests[0] and 'text' not in requests[0]


def test_cancelled_mcp_approval_does_not_make_a_request(monkeypatch):
    session = Session()
    pending = {'type': 'mcp_approval_request', 'id': 'approval_1',
               'server_label': 'demo', 'name': 'echo', 'arguments': '{}'}
    provider, requests = make_provider(monkeypatch, OpenAIResponsesProvider, session, [response([pending])])
    session.data['__interaction_broker__'] = SimpleNamespace(prompt=lambda need: None)
    TurnRunner(session)._record_assistant(provider.chat())
    session.chat.add('Continue')
    assert 'MCP approval cancelled' in provider.chat()
    assert len(requests) == 1


def test_responses_omits_images_for_nonvision_models(monkeypatch):
    session = Session({'vision': False})
    image = SimpleNamespace(get=lambda: {'mime_type': 'image/png', 'content': 'aW1hZ2U='})
    session.chat.conversation[0]['context'] = [{'type': 'image', 'context': image}]
    provider, requests = make_provider(monkeypatch, OpenAIResponsesProvider, session, [response()])
    provider.chat()
    assert requests[0]['input'] == [{'role': 'user', 'content': 'Please echo hello'}]
    assert all(part['type'] == 'text' for part in provider.get_messages()[0]['content'])


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
def test_older_sdk_signatures_still_forward_new_wire_fields(monkeypatch, provider_class):
    session = Session({'verbosity': 'Low', 'prompt_cache_key': 'cache-key'})
    reply = chat_response() if provider_class is OpenAIProvider else response([message()])
    provider, requests = make_provider(monkeypatch, provider_class, session, [reply])
    if provider_class is OpenAIProvider:
        create = provider.client.chat.completions.create

        def older_create(*, model, messages, extra_body=None):
            return create(model=model, messages=messages, extra_body=extra_body)

        provider.client.chat.completions.create = older_create
    else:
        create = provider._client.responses.create

        def older_create(*, model, input, extra_body=None):
            return create(model=model, input=input, extra_body=extra_body)

        provider._client.responses.create = older_create
    assert provider.chat() == 'Done'
    if provider_class is OpenAIProvider:
        assert requests[0]['verbosity'] == 'low'
    else:
        assert requests[0]['text']['verbosity'] == 'low'
        assert requests[0]['store'] is False
        assert requests[0]['prompt_cache_key'] == 'cache-key'


def cache_write_reply(provider_class, stream=False, headers=None, writes=300):
    """Return a realistic cache usage response, including HTTP accounting headers."""
    details = {'cached_tokens': 400, 'cache_write_tokens': writes}
    if provider_class is OpenAIProvider:
        usage = {'prompt_tokens': 1000, 'completion_tokens': 10, 'total_tokens': 1010,
                 'prompt_tokens_details': details}
        reply = (chat_sse([chat_chunk({'content': 'Done'}, finish_reason='stop'),
                          chat_chunk(choices=[], usage=usage)])
                 if stream else chat_response(usage=usage))
    else:
        usage = {'input_tokens': 1000, 'output_tokens': 10, 'total_tokens': 1010,
                 'input_tokens_details': details}
        final = response([message()], usage=usage)
        reply = sse([{'type': 'response.completed', 'response': final}]) if stream else final
    if stream:
        return httpx.Response(200, headers={'content-type': 'text/event-stream', **(headers or {})},
                              text=reply)
    return httpx.Response(200, headers=headers or {}, json=reply)


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('headers,expected_tiers,input_cost', [
    ({'Msh-Usage-Cache-Write-Tokens-5m': '100',
      'Msh-Usage-Cache-Write-Tokens-1h': '200'}, (100, 200), 0.00262),
    ({'Msh-Usage-Cache-Write-Tokens-5m': '300'}, (300, 0), 0.00222),
    ({'Msh-Usage-Cache-Write-Tokens-1h': '300'}, (0, 300), 0.00282),
    ({}, (0, 0), 0.00252),
    ({'Msh-Usage-Cache-Write-Tokens-5m': '100'}, (100, 0), 0.00242),
    ({'Msh-Usage-Cache-Write-Tokens-5m': 'invalid',
      'Msh-Usage-Cache-Write-Tokens-1h': '-20'}, (0, 0), 0.00252),
])
def test_cache_write_tiers_use_actual_headers_without_double_counting(
        monkeypatch, provider_class, stream, headers, expected_tiers, input_cost):
    session = Session({'stream': stream, 'price_in': 3, 'price_cache_in': 0.3,
                       'price_out': 12, 'price_cache_write': 5,
                       'price_cache_write_5m': 4, 'price_cache_write_1h': 6,
                       'extra_body': {'prompt_cache_options': {'mode': 'implicit', 'ttl': '1h'}}})
    reply = cache_write_reply(provider_class, stream, headers)
    provider, requests = make_provider(monkeypatch, provider_class, session, [reply])
    text = ''.join(provider.stream_chat()) if stream else provider.chat()
    assert text == 'Done'
    stats = provider.get_usage()
    assert stats['turn_in'] == 1000 and stats['turn_total'] == 1010
    assert stats['turn_cached'] == 400 and stats['turn_cache_writes'] == 300
    assert (stats['turn_cache_writes_5m'], stats['turn_cache_writes_1h']) == expected_tiers
    assert provider.get_cost() == {'input_cost': input_cost, 'output_cost': 0.00012,
                                   'total_cost': round(input_cost + 0.00012, 6)}
    assert requests[0]['prompt_cache_options'] == {'mode': 'implicit', 'ttl': '1h'}
    # Restoring/rebuilding must preserve the original request's rates and subsets.
    session.params.update(price_in=100, price_cache_write_1h=100)
    provider.reset_usage()
    provider.set_usage(stats)
    assert provider.get_usage()['total_cache_writes'] == 300
    assert provider.get_usage()['total_cache_writes_1h'] == expected_tiers[1]
    assert provider.get_cost()['input_cost'] == input_cost


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
def test_cache_write_headers_do_not_leak_into_the_next_request(monkeypatch, provider_class):
    session = Session({'price_in': 3, 'price_cache_in': 0.3, 'price_cache_write_1h': 6})
    provider, _ = make_provider(monkeypatch, provider_class, session, [
        cache_write_reply(provider_class, headers={'Msh-Usage-Cache-Write-Tokens-1h': '300'}),
        cache_write_reply(provider_class, writes=0), httpx.ConnectError('Offline failure'),
    ])
    assert provider.chat() == 'Done'
    session.params.update(price_in=4, price_cache_in=0.4, price_cache_write_1h=12)
    assert provider.chat() == 'Done'
    assert provider.get_usage()['turn_cache_writes_1h'] == 0
    assert provider.get_usage()['total_cache_writes_1h'] == 300
    assert provider.get_cost()['input_cost'] == 0.00538
    provider.chat()
    assert 'turn_cache_writes_1h' not in provider.get_usage()
    assert provider.get_usage()['total_cache_writes_1h'] == 300


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
def test_cache_write_missing_usage_count_can_use_headers(monkeypatch, provider_class):
    session = Session({'price_in': 3, 'price_cache_in': 0.3, 'price_cache_write_1h': 6})
    provider, _ = make_provider(monkeypatch, provider_class, session, [cache_write_reply(
        provider_class, headers={'Msh-Usage-Cache-Write-Tokens-1h': '300'}, writes=None)])
    assert provider.chat() == 'Done'
    assert provider.get_usage()['turn_cache_writes'] == 300
    assert provider.get_cost()['input_cost'] == 0.00282


@pytest.mark.parametrize('provider_class', [OpenAIProvider, OpenAIResponsesProvider])
def test_cache_write_unconfigured_rates_preserve_input_pricing(monkeypatch, provider_class):
    session = Session({'price_in': 3, 'price_cache_in': 0.3})
    provider, _ = make_provider(monkeypatch, provider_class, session, [cache_write_reply(
        provider_class, headers={'Msh-Usage-Cache-Write-Tokens-1h': '300'})])
    assert provider.chat() == 'Done'
    assert provider.get_cost()['input_cost'] == 0.00192


def test_moonshot_alias_loads_the_shared_provider_without_subclass():
    from configparser import ConfigParser
    from pathlib import Path
    from component_registry import ComponentRegistry

    root = Path(__file__).resolve().parents[2]
    config = ConfigParser(interpolation=None)
    config.read(root / 'config.ini')
    registry = ComponentRegistry(SimpleNamespace(base_config=config))
    cls = registry.load_provider_class('Moonshot')
    assert cls is not None and cls.__name__ == 'OpenAIProvider'
    assert not (root / 'providers' / 'moonshot_provider.py').exists()
