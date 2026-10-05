"""Verify Gemini's actual SDK JSON and streamed responses without network access."""

import json
from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest
from google import genai
from google.genai import types

from core.cancellation import CancellationToken
from core.turns import TurnRunner
from contexts.chat_context import ChatContext
from providers.google_provider import GoogleProvider


class Session:
    def __init__(self, params=None, tools=False):
        self.params = {'model_name': 'offline-model', **(params or {})}
        self.chat = ChatContext(self)
        self.chat.add('Please echo hello')
        self.tools = tools
        self.data = {'__tool_api_to_cmd__': {'echo_api': 'mcp:demo.echo'}}
        self.flags = {}
        self.executed = []
        self.ui = SimpleNamespace(emit=lambda *a, **k: None,
                                  capabilities=SimpleNamespace(blocking=False))
        self.utils = SimpleNamespace(output=SimpleNamespace(
            write=lambda *a, **k: None, warning=lambda *a, **k: None,
            stop_spinner=lambda: None, spinner=lambda *a, **k: nullcontext()))
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


def response(parts=None, finish='STOP', usage=None, index=0):
    result = {'candidates': [{'index': index, 'content': {
        'role': 'model', 'parts': parts or [{'text': 'Done'}]}}]}
    if finish is not None:
        result['candidates'][0]['finishReason'] = finish
    if usage is not None:
        result['usageMetadata'] = usage
    return result


def function_call(ident='call_1', value='hello', signature='c2lnbmF0dXJl'):
    fn = {'name': 'echo_api', 'args': {'value': value}}
    if ident is not None:
        fn['id'] = ident
    return {'functionCall': fn, 'thoughtSignature': signature}


class Wire(httpx.SyncByteStream):
    """Track the HTTP response itself, including early close and interruptions."""

    def __init__(self, events, fail=False, on_chunk=None):
        self.events = events
        self.fail = fail
        self.on_chunk = on_chunk
        self.closed = False

    def __iter__(self):
        for event in self.events:
            if self.on_chunk:
                self.on_chunk()
            yield ('data: ' + json.dumps(event) + '\n\n').encode()
        if self.fail:
            raise httpx.ReadError('offline interruption')

    def close(self):
        self.closed = True


def make_provider(monkeypatch, replies, params=None, tools=True):
    session = Session({'api_key': 'offline-test', 'vertexai': False, 'max_retries': 0,
                       **(params or {})}, tools=tools)
    requests, clients = [], []
    pending = iter(replies)
    real_client = genai.Client

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        # Model Gemini 3's current-turn signature requirement. An unchanged native
        # call must retain its signed first part; fallback histories must be text.
        for content in payload.get('contents', []):
            first_call = next((part for part in content['parts'] if 'functionCall' in part), None)
            if first_call is not None and not first_call.get('thoughtSignature'):
                return httpx.Response(400, json={'error': {'code': 400,
                    'status': 'INVALID_ARGUMENT', 'message': 'Missing thought signature'}})
        reply = next(pending)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, Wire):
            return httpx.Response(200, stream=reply, headers={'content-type': 'text/event-stream'})
        return httpx.Response(200, json=reply)

    def client(**options):
        http = options['http_options']
        http.client_args['transport'] = httpx.MockTransport(handle)
        result = real_client(**options)
        clients.append(result)
        return result

    monkeypatch.setattr(genai, 'Client', client)
    provider = GoogleProvider(session)
    session.provider = provider
    return provider, session, requests, clients


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('ident', [None, 'native_call'])
def test_signed_tool_round_trip_and_checkpoint(monkeypatch, stream, ident):
    native = [{'text': 'Thought summary', 'thought': True},
              {'text': 'I will echo that.'}, function_call(ident)]
    result = response(native)
    replies = [Wire([result]) if stream else result, response()]
    p, s, req, _ = make_provider(monkeypatch, replies)
    output = ''.join(p.stream_chat()) if stream else p.chat()
    assert output == 'I will echo that.'
    assert p.get_current_reasoning() == 'Thought summary'
    runner = TurnRunner(s)
    runner._record_assistant(output)
    assert runner._execute_tools(output)
    assert s.executed == [{'value': 'hello'}]
    assert p.get_tool_calls() == []
    assistant = s.chat.get()[1]
    checkpoint = json.loads(json.dumps(assistant))
    assert checkpoint['google_content']['parts'][2]['thought_signature'] == 'c2lnbmF0dXJl'
    assert checkpoint['reasoning_content'] == 'Thought summary'
    assert assistant['tool_calls'][0]['api_name'] == 'echo_api'
    assert assistant['tool_calls'][0]['google_call_id'] == ident
    prefix = p._request_prefix
    display = p.get_messages()
    assert display[1]['parts'] == checkpoint['google_content']['parts']
    assert p._request_prefix == prefix
    # A JSON checkpoint and a rebuilt provider must be sufficient for native replay.
    s.chat.conversation[1] = checkpoint
    p.cleanup()
    rebuilt = GoogleProvider(s)
    s.provider = rebuilt
    assert rebuilt.chat() == 'Done'
    assert req[1]['contents'][1]['parts'] == native
    expected = {'name': 'echo_api', 'response': {'output': 'OK'}}
    if ident is not None:
        expected['id'] = ident
    assert req[1]['contents'][2]['parts'] == [{'functionResponse': expected}]
    rebuilt.cleanup()


@pytest.mark.parametrize('mutation', ['edit_user', 'edit_assistant', 'trim', 'model',
                                     'thinking', 'tool', 'system', 'call_args', 'call_id'])
def test_signed_replay_rejects_changed_history(monkeypatch, mutation):
    p, s, req, _ = make_provider(monkeypatch, [response([function_call()]), response()])
    runner = TurnRunner(s)
    runner._record_assistant(p.chat())
    runner._execute_tools('')
    if mutation == 'edit_user':
        s.chat.conversation[0]['message'] = 'Edited question'
    elif mutation == 'edit_assistant':
        s.chat.conversation[1]['message'] = 'Edited answer'
    elif mutation == 'trim':
        s.chat.conversation.pop(0)
    elif mutation == 'model':
        s.params['model_name'] = 'changed-model'
    elif mutation == 'thinking':
        s.params['thinking_config'] = {'thinking_level': 'low'}
    elif mutation == 'tool':
        s.commands.get_tool_specs = lambda: []
    elif mutation == 'system':
        s.params['system_instruction'] = 'Changed system'
    elif mutation == 'call_args':
        s.chat.conversation[1]['tool_calls'][0]['arguments'] = {'value': 'changed'}
    elif mutation == 'call_id':
        s.chat.conversation[1]['tool_calls'][0]['google_call_id'] = 'changed'
    assert p.chat() == 'Done'
    assert not any('thoughtSignature' in part for content in req[1]['contents']
                   for part in content['parts'])
    assert any('Historical tool call' in part.get('text', '')
               for content in req[1]['contents'] for part in content['parts'])
    p.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_parallel_results_grouped_with_matching_ids(monkeypatch, stream):
    parts = [function_call('a', 'one'), function_call('b', 'two', signature='')]
    first = (Wire([response([parts[0]], finish=None), response([parts[1]])])
             if stream else response(parts))
    p, s, req, _ = make_provider(monkeypatch, [first, response()])
    out = ''.join(p.stream_chat()) if stream else p.chat()
    runner = TurnRunner(s)
    runner._record_assistant(out)
    assert runner._execute_tools(out)
    assert s.executed == [{'value': 'one'}, {'value': 'two'}]
    p.chat()
    results = req[1]['contents'][2]['parts']
    assert [part['functionResponse']['id'] for part in results] == ['a', 'b']
    assert len(req[1]['contents']) == 3
    p.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_failed_request_cannot_reexecute_stale_call(monkeypatch, stream):
    error = Wire([], fail=True) if stream else httpx.ReadError('offline failure')
    p, s, _, _ = make_provider(monkeypatch, [response([function_call()], usage={
        'promptTokenCount': 10}), error])
    p.chat()
    assert p.get_tool_calls()
    text = ''.join(p.stream_chat()) if stream else p.chat()
    assert 'error' in text.lower()
    runner = TurnRunner(s)
    runner._record_assistant(text)
    assert not runner._execute_tools(text)
    assert s.executed == []
    assert p.get_full_response() is None
    assert p.get_finish_reason() == 'error'
    assert p.get_usage()['turn_in'] == 0
    assert p.get_usage()['total_in'] == 10
    p.cleanup()


@pytest.mark.parametrize('stop', ['SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT',
                                  'MALFORMED_FUNCTION_CALL', 'UNEXPECTED_TOOL_CALL', 'OTHER'])
@pytest.mark.parametrize('stream', [False, True])
def test_blocked_or_failed_completion_cannot_execute_tools(monkeypatch, stop, stream):
    result = response([function_call()], finish=stop)
    p, s, _, _ = make_provider(monkeypatch, [Wire([result]) if stream else result])
    out = ''.join(p.stream_chat()) if stream else p.chat()
    assert 'Gemini' in out
    assert p.get_tool_calls() == []
    assert not TurnRunner(s)._execute_tools('%%CMD%% command="echo"\n%%END%%')
    p.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_token_limit_marks_calls_truncated(monkeypatch, stream):
    result = response([function_call()], finish='MAX_TOKENS')
    p, s, _, _ = make_provider(monkeypatch, [Wire([result]) if stream else result])
    out = ''.join(p.stream_chat()) if stream else p.chat()
    runner = TurnRunner(s)
    runner._record_assistant(out)
    runner._execute_tools(out)
    assert s.executed == []
    assert p.get_finish_reason() == 'length'
    p.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_prompt_block_explained_and_usage_retained(monkeypatch, stream):
    blocked = {'candidates': [], 'promptFeedback': {'blockReason': 'SAFETY'},
               'usageMetadata': {'promptTokenCount': 12, 'totalTokenCount': 12}}
    p, _, _, _ = make_provider(monkeypatch, [Wire([blocked]) if stream else blocked])
    out = ''.join(p.stream_chat()) if stream else p.chat()
    assert 'blocked' in out.lower()
    assert p.get_finish_reason() == 'content_filter'
    assert p.get_usage()['turn_in'] == 12
    p.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_selects_one_candidate_consistently(monkeypatch, stream):
    r = response([{'text': 'Selected'}, function_call('a', 'first')])
    r['candidates'].insert(0, response([{'text': 'Alternative'}, function_call('b', 'second')],
                                      index=1)['candidates'][0])
    p, _, _, _ = make_provider(monkeypatch, [Wire([r]) if stream else r], {'candidate_count': 2})
    out = ''.join(p.stream_chat()) if stream else p.chat()
    assert out == 'Selected'
    assert [call['id'] for call in p.get_tool_calls()] == ['a']
    p.cleanup()


def test_stream_collects_text_calls_signatures_and_final_usage(monkeypatch):
    events = [response([{'text': 'Before'}, function_call('a')], finish=None),
              response([{'text': 'After'}, function_call('b', 'two')], finish=None),
              response([{'text': '', 'thoughtSignature': 'dGFpbA=='}], usage={
                  'promptTokenCount': 100, 'cachedContentTokenCount': 80,
                  'candidatesTokenCount': 10, 'thoughtsTokenCount': 30, 'totalTokenCount': 140})]
    wire = Wire(events)
    p, _, _, _ = make_provider(monkeypatch, [wire])
    assert ''.join(p.stream_chat()) == 'BeforeAfter'
    assert [call['id'] for call in p.get_tool_calls()] == ['a', 'b']
    assert p.get_usage()['turn_out'] == 40
    assert p.get_usage()['turn_reasoning'] == 30
    native = p.get_assistant_metadata()['google_content']['parts']
    assert len(native) == 5
    assert native[-1]['thought_signature'] == 'dGFpbA=='
    assert wire.closed
    assert len(p.get_full_response().candidates[0].content.parts) == 5
    p.cleanup()


@pytest.mark.parametrize('fail', [False, True])
def test_unfinished_stream_never_executes_and_retains_observed_usage(monkeypatch, fail):
    wire = Wire([response([function_call()], finish=None, usage={
        'promptTokenCount': 10, 'candidatesTokenCount': 2})], fail=fail)
    p, s, _, _ = make_provider(monkeypatch, [wire])
    assert 'error' in ''.join(p.stream_chat()).lower() or p.get_finish_reason() == 'error'
    assert not TurnRunner(s)._execute_tools('')
    assert p.get_tool_calls() == []
    assert p.get_usage()['turn_in'] == 10
    assert p.get_usage()['turn_out'] == 2
    assert p.get_assistant_metadata() == {}
    assert p.get_full_response() is not None
    assert wire.closed
    p.cleanup()


def test_generator_close_releases_actual_http_response(monkeypatch):
    wire = Wire([response([{'text': 'first'}], finish=None, usage={'promptTokenCount': 10}),
                 response([function_call()])])
    p, _, _, clients = make_provider(monkeypatch, [wire])
    generator = p.stream_chat()
    assert next(generator) == 'first'
    generator.close()
    assert wire.closed
    assert p.get_finish_reason() == 'cancelled'
    assert p.get_tool_calls() == []
    assert p.get_usage()['turn_in'] == 10
    p.cleanup()
    assert clients[0]._api_client._httpx_client.is_closed


def test_cancellation_token_closes_connection_before_next_chunk(monkeypatch):
    wire = Wire([response([{'text': 'first'}], finish=None), response([function_call()])])
    p, s, _, _ = make_provider(monkeypatch, [wire])
    token = CancellationToken()
    s.get_cancellation_token = lambda: token
    generator = p.stream_chat()
    assert next(generator) == 'first'
    token.cancel('user')
    assert wire.closed
    assert list(generator) == []
    assert p.get_finish_reason() == 'cancelled'
    assert p.get_tool_calls() == []
    p.cleanup()


def test_already_cancelled_turn_never_initializes_client(monkeypatch):
    p, s, req, clients = make_provider(monkeypatch, [])
    token = CancellationToken()
    token.cancel('user')
    s.get_cancellation_token = lambda: token
    assert p.chat() == ''
    assert list(p.stream_chat()) == []
    assert req == clients == []
    assert p.get_finish_reason() == 'cancelled'


def test_old_cancellation_token_cannot_close_next_request(monkeypatch):
    wire = Wire([response([{'text': 'next'}], finish=None), response()])
    p, s, _, _ = make_provider(monkeypatch, [response(), wire])
    token = CancellationToken()
    s.get_cancellation_token = lambda: token
    p.chat()
    s.get_cancellation_token = lambda: None
    generator = p.stream_chat()
    next(generator)
    token.cancel('late cancellation')
    assert not wire.closed
    assert ''.join(generator) == 'Done'
    p.cleanup()


def test_reset_clears_unconsumed_calls(monkeypatch):
    p, _, _, _ = make_provider(monkeypatch, [response([function_call()],
                                                    usage={'promptTokenCount': 10})])
    p.chat()
    p.reset_usage()
    assert p.get_tool_calls() == []
    assert p.get_full_response() is None
    assert p.get_usage()['total_in'] == 0
    assert p.get_cost()['total_cost'] == 0
    p.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_cache_thinking_cost_and_usage_restore(monkeypatch, stream):
    result = response(usage={'promptTokenCount': 100, 'cachedContentTokenCount': 80,
        'candidatesTokenCount': 10, 'thoughtsTokenCount': 30, 'totalTokenCount': 140,
        'toolUsePromptTokenCount': 5})
    p, s, _, _ = make_provider(monkeypatch, [Wire([result]) if stream else result, response()],
        {'price_unit': 1000000, 'price_in': 2, 'price_cache_in': .2, 'price_out': 12})
    ''.join(p.stream_chat()) if stream else p.chat()
    usage = p.get_usage()
    assert usage['total_in'] == 100
    assert usage['total_out'] == 40
    assert usage['total_cached'] == 80
    assert usage['total_reasoning'] == 30
    assert usage['total_candidates'] == 10
    assert usage['total_tool_prompt'] == 5
    assert usage['total_tokens'] == 140
    assert p.get_cost() == {'input_cost': .000056, 'output_cost': .00048,
                            'cache_read_cost': .000016, 'cache_savings': .000144,
                            'total_cost': .000536}
    s.params.update(price_in=20, price_out=120)
    assert p.get_cost()['total_cost'] == .000536
    rebuilt = GoogleProvider(s)
    rebuilt.set_usage(usage)
    assert rebuilt.get_cost() == p.get_cost()
    assert rebuilt.get_usage()['total_out'] == 40
    rebuilt.cleanup()
    p.cleanup()


def test_stream_usage_updates_are_cumulative_not_additive(monkeypatch):
    wire = Wire([response([{'text': 'one'}], finish=None, usage={
        'promptTokenCount': 100, 'cachedContentTokenCount': 80, 'candidatesTokenCount': 3}),
        response([{'text': 'two'}], usage={'candidatesTokenCount': 10, 'thoughtsTokenCount': 30})])
    p, _, _, _ = make_provider(monkeypatch, [wire])
    assert ''.join(p.stream_chat()) == 'onetwo'
    assert p.get_usage()['total_in'] == 100
    assert p.get_usage()['total_out'] == 40
    assert p.get_usage()['total_cached'] == 80
    assert p.get_usage()['total_tokens'] == 140
    p.cleanup()


@pytest.mark.parametrize('unit', [0, -1, 'invalid', None])
def test_invalid_pricing_remains_unknown_across_rebuild(monkeypatch, unit):
    p, s, _, _ = make_provider(monkeypatch, [response(usage={'promptTokenCount': 10})],
                             {'price_unit': unit})
    p.chat()
    assert p.get_cost() is None
    rebuilt = GoogleProvider(s)
    rebuilt.set_usage(p.get_usage())
    assert rebuilt.get_cost() is None
    rebuilt.cleanup()
    p.cleanup()


def test_native_request_controls_and_alias_priority(monkeypatch):
    params = {'max_tokens': 64, 'max_completion_tokens': 96, 'max_output_tokens': 128,
        'thinking_config': '{"thinking_level":"low", "include_thoughts":true}',
        'reasoning_effort': 'high', 'response_mime_type': 'application/json',
        'response_json_schema': {'type': 'object', 'properties': {'answer': {'type': 'string'}}},
        'seed': 42, 'temperature': .4, 'top_p': .9, 'stop_sequences': ['END'],
        'tool_config': {'function_calling_config': {'mode': 'NONE'}}}
    p, _, req, _ = make_provider(monkeypatch, [response()], params)
    assert p.chat() == 'Done'
    generation = req[0]['generationConfig']
    assert generation['maxOutputTokens'] == 128
    thinking = generation['thinkingConfig']
    assert thinking.get('thinkingLevel', thinking.get('thinking_level')).lower() == 'low'
    assert thinking.get('includeThoughts', thinking.get('include_thoughts')) is True
    assert generation['responseJsonSchema'] == params['response_json_schema']
    assert generation['responseMimeType'] == 'application/json'
    assert generation['seed'] == 42
    assert generation['stopSequences'] == ['END']
    assert req[0]['toolConfig']['functionCallingConfig']['mode'] == 'NONE'
    p.cleanup()


@pytest.mark.parametrize('params, expected', [
    ({'max_tokens': '64'}, 64), ({'max_tokens': 64, 'max_completion_tokens': 96}, 96),
    ({'max_tokens': 64, 'max_output_tokens': 0}, 0),
])
def test_token_cap_aliases(monkeypatch, params, expected):
    p, _, req, _ = make_provider(monkeypatch, [response()], params)
    p.chat()
    assert req[0]['generationConfig']['maxOutputTokens'] == expected
    p.cleanup()


@pytest.mark.parametrize('choice, mode, names', [
    ('none', 'NONE', None), ('auto', 'AUTO', None), ('required', 'ANY', None),
    ({'type': 'function', 'function': {'name': 'echo_api'}}, 'ANY', ['echo_api']),
])
def test_tool_choice_mapping(monkeypatch, choice, mode, names):
    p, _, req, _ = make_provider(monkeypatch, [response()], {'tool_choice': choice})
    p.chat()
    choice = req[0]['toolConfig']['functionCallingConfig']
    assert choice['mode'] == mode
    assert choice.get('allowedFunctionNames') == names
    p.cleanup()


@pytest.mark.parametrize('excluded', [['temperature', 'thinking_config', 'tools', 'max_output_tokens'],
    'temperature, thinking_config, tools, max_output_tokens'])
def test_exclusions_apply_to_aliases_native_fields_and_extra_body(monkeypatch, excluded):
    p, _, req, _ = make_provider(monkeypatch, [response()], {
        'excluded_parameters': excluded, 'temperature': .7, 'max_tokens': 128,
        'thinking_level': 'high', 'extra_body': {'tools': [{'googleSearch': {}}],
            'generationConfig': {'temperature': .3, 'thinkingConfig': {'thinkingLevel': 'low'},
                                 'maxOutputTokens': 100, 'seed': 42}}})
    assert p.chat() == 'Done'
    assert req[0]['generationConfig'] == {'seed': 42}
    assert 'tools' not in req[0]
    p.cleanup()


@pytest.mark.parametrize('mode', ['none', 'pseudo'])
def test_tool_mode_hard_gates_native_and_extra_body_tools(monkeypatch, mode):
    p, s, req, _ = make_provider(monkeypatch, [response()], {
        'tools': [{'google_search': {}}], 'tool_choice': 'required',
        'extra_body': {'tools': [{'googleSearch': {}}], 'toolConfig': {
            'functionCallingConfig': {'mode': 'ANY'}}}})
    s.get_effective_tool_mode = lambda: mode
    p.chat()
    assert 'tools' not in req[0]
    assert 'toolConfig' not in req[0]
    p.cleanup()


def test_native_tools_merge_and_canonical_schema_unchanged(monkeypatch):
    schema = {'type': 'object', 'properties': {'value': {'type': ['string', 'null']}},
              'required': ['value'], 'additionalProperties': False,
              'anyOf': [{'required': ['value']}], '$defs': {'example': {'type': 'string'}}}
    spec = {'name': 'echo_api', 'description': 'Native echo', 'parameters': schema}
    p, s, req, _ = make_provider(monkeypatch, [response()], {'tools': [{'google_search': {}}]})
    s.commands.get_tool_specs = lambda: [spec]
    original = deepcopy(spec)
    assert p.chat() == 'Done'
    assert req[0]['tools'][0] == {'googleSearch': {}}
    decl = req[0]['tools'][1]['functionDeclarations'][0]
    assert decl.get('parametersJsonSchema', decl.get('parameters_json_schema')) == schema
    assert spec == original
    p.cleanup()


def test_configured_duplicate_function_wins(monkeypatch):
    custom = {'name': 'echo_api', 'description': 'Custom', 'parameters_json_schema': {
        'type': 'object', 'properties': {}}}
    p, _, req, _ = make_provider(monkeypatch, [response()], {
        'tools': [{'function_declarations': [custom]}]})
    p.chat()
    decls = [decl for tool in req[0]['tools'] for decl in tool.get('functionDeclarations', [])]
    assert len(decls) == 1
    assert decls[0]['description'] == 'Custom'
    p.cleanup()


def test_extra_body_is_parsed_copied_and_forwarded(monkeypatch):
    body = {'generationConfig': {'seed': 42}, 'customField': {'enabled': True}}
    p, s, req, _ = make_provider(monkeypatch, [response()], {
        'extra_body': repr(body), 'http_options': {'extra_body': {'customSecond': 'value'}}})
    p.chat()
    assert req[0]['generationConfig']['seed'] == 42
    assert req[0]['customField'] == {'enabled': True}
    assert req[0]['customSecond'] == 'value'
    assert s.params['extra_body'] == repr(body)
    p.cleanup()


@pytest.mark.parametrize('vision', [False, 'false', True])
def test_vision_gate_matches_introspection_and_request(monkeypatch, vision):
    p, s, req, _ = make_provider(monkeypatch, [response()], {'vision': vision})
    s.chat.conversation[0]['context'] = [{'type': 'image', 'context': SimpleNamespace(get=lambda: {
        'mime_type': 'image/png', 'content': 'aW1hZ2U='})}]
    display = p.get_messages()
    assert any('inline_data' in part for part in display[0]['parts']) == (vision is True)
    p.chat()
    assert any('inlineData' in part for part in req[0]['contents'][0]['parts']) == (vision is True)
    p.cleanup()


def test_explicit_cache_owns_system_and_tools(monkeypatch):
    p, _, req, _ = make_provider(monkeypatch, [response()], {
        'cached_content': 'cachedContents/example', 'system_instruction': 'System prompt'})
    p.chat()
    assert req[0]['cachedContent'] == 'cachedContents/example'
    assert 'tools' not in req[0]
    assert 'systemInstruction' not in req[0]
    p.cleanup()


def test_client_options_and_request_timeout_units(monkeypatch):
    captured = []
    monkeypatch.setattr(genai, 'Client', lambda **kwargs: captured.append(kwargs) or SimpleNamespace())
    session = Session({'api_key': 'offline', 'vertexai': 'true', 'project': 'example',
        'location': 'us-central1', 'base_url': 'https://offline.example', 'api_version': 'v1',
        'timeout': 2.5, 'max_retries': 2, 'default_headers': '{"X-Test":"yes"}'})
    p = GoogleProvider(session)
    p._ensure_client()
    assert captured[0]['vertexai'] is True
    assert captured[0]['project'] == 'example'
    http = captured[0]['http_options']
    assert http.base_url == 'https://offline.example'
    assert http.api_version == 'v1'
    assert http.timeout == 2500
    assert http.retry_options.attempts == 3
    assert http.headers['X-Test'] == 'yes'
    p.client = None


@pytest.mark.parametrize('stream', [False, True])
def test_unspecified_prompt_feedback_does_not_block_valid_response(monkeypatch, stream):
    r = response()
    r['promptFeedback'] = {'blockReason': 'BLOCKED_REASON_UNSPECIFIED'}
    p, _, _, _ = make_provider(monkeypatch, [Wire([r]) if stream else r])
    assert (''.join(p.stream_chat()) if stream else p.chat()) == 'Done'
    assert p.get_finish_reason() == 'stop'
    p.cleanup()


def test_truncated_textual_command_cannot_execute(monkeypatch):
    p, s, _, _ = make_provider(monkeypatch, [response([{'text': 'partial command'}],
                                                    finish='MAX_TOKENS')])
    s.commands.parse_commands = lambda text: ['echo']
    s.commands.run = lambda text: s.executed.append('textual command')
    p.chat()
    assert not TurnRunner(s)._execute_tools('partial command')
    assert s.executed == []
    p.cleanup()


@pytest.mark.parametrize('args', [[], 'invalid'])
def test_sdk_rejects_nonobject_arguments_without_executing(monkeypatch, args):
    call = function_call()
    call['functionCall']['args'] = args
    p, s, _, _ = make_provider(monkeypatch, [response([call])])
    p.chat()
    assert p.get_finish_reason() == 'error'
    assert p.get_tool_calls() == []
    assert not TurnRunner(s)._execute_tools('')
    p.cleanup()


def test_zero_argument_function_is_valid(monkeypatch):
    r = response([{'functionCall': {'name': 'echo_api'}, 'thoughtSignature': 'c2lnbmF0dXJl'}])
    p, s, _, _ = make_provider(monkeypatch, [r])
    s.commands.get_tool_specs = lambda: [{'name': 'echo_api', 'parameters': {
        'type': 'object', 'properties': {}}}]
    runner = TurnRunner(s)
    runner._record_assistant(p.chat())
    runner._execute_tools('')
    assert s.executed == [{}]
    p.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_partial_function_calls_are_not_executable(monkeypatch, stream):
    call = function_call()
    call['functionCall']['willContinue'] = True
    p, _, _, _ = make_provider(monkeypatch, [Wire([response([call])]) if stream else response([call])])
    ''.join(p.stream_chat()) if stream else p.chat()
    assert p.get_tool_calls()[0]['truncated']
    assert p.get_assistant_metadata() == {}
    p.cleanup()


def test_duplicate_native_call_ids_are_rejected(monkeypatch):
    p, _, _, _ = make_provider(monkeypatch, [response([function_call('same'), function_call('same')])])
    p.chat()
    calls = p.get_tool_calls()
    assert all(call['truncated'] for call in calls)
    assert p.get_assistant_metadata() == {}
    p.cleanup()


def test_synthetic_ids_are_unique_between_responses(monkeypatch):
    p, _, _, _ = make_provider(monkeypatch, [response([function_call(None)]),
                                          response([function_call(None)])])
    p.chat()
    first = p.get_tool_calls()[0]
    p.chat()
    second = p.get_tool_calls()[0]
    assert first['id'] != second['id']
    assert first['google_call_id'] is second['google_call_id'] is None
    p.cleanup()


def test_nonstream_after_stream_clears_old_calls_and_raw_output(monkeypatch):
    p, _, _, _ = make_provider(monkeypatch, [Wire([response([function_call()])]), response()])
    list(p.stream_chat())
    assert p.chat() == 'Done'
    assert p.get_tool_calls() == []
    assert p.get_full_response().text == 'Done'
    p.cleanup()


def test_native_content_without_tools_is_replayed_exactly(monkeypatch):
    native = [{'text': 'summary', 'thought': True}, {'text': 'First'}, {'text': 'Second'},
              {'text': '', 'thoughtSignature': 'c2lnbmF0dXJl'}]
    p, s, req, _ = make_provider(monkeypatch, [response(native), response()])
    runner = TurnRunner(s)
    assert p.chat() == 'FirstSecond'
    runner._record_assistant('FirstSecond')
    s.chat.add('Continue')
    p.chat()
    assert req[1]['contents'][1]['parts'] == native
    p.cleanup()


def test_native_replay_rejects_changed_backend(monkeypatch):
    p, s, req, _ = make_provider(monkeypatch, [response([function_call()]), response()])
    runner = TurnRunner(s)
    runner._record_assistant(p.chat())
    runner._execute_tools('')
    s.params['api_version'] = 'v1'
    p.chat()
    assert all('thoughtSignature' not in part for part in req[1]['contents'][1]['parts'])
    p.cleanup()


def test_old_checkpoint_recovers_api_name_and_native_id(monkeypatch):
    p, s, req, _ = make_provider(monkeypatch, [response()])
    s.chat.add('', role='assistant', extra={'tool_calls': [{
        'id': 'native_old_id', 'name': 'mcp:demo.echo', 'arguments': {'value': 'hello'}}]})
    s.chat.add('OK', role='tool', extra={'tool_call_id': 'native_old_id'})
    p.chat()
    assert p.get_finish_reason() == 'stop'
    call = req[0]['contents'][1]['parts'][0]['text']
    result = req[0]['contents'][2]['parts'][0]['text']
    assert 'echo_api' in call and 'echo_api' in result
    assert 'native_old_id' in call and 'native_old_id' in result
    assert 'already executed' in call
    p.cleanup()


def test_system_introspection_and_actual_request_match(monkeypatch):
    p, _, req, _ = make_provider(monkeypatch, [response()], {'system_instruction': 'System'})
    assert p.get_messages()[0] == {'role': 'system', 'parts': [{'text': 'System'}]}
    p.chat()
    assert req[0]['systemInstruction']['parts'] == [{'text': 'System'}]
    p.cleanup()


def test_extra_body_nested_merges_and_introspection(monkeypatch):
    p, _, req, _ = make_provider(monkeypatch, [response()], {
        'extra_body': {'generationConfig': {'seed': 42}, 'systemInstruction': {
            'parts': [{'text': 'Actual system'}]}},
        'http_options': {'extra_body': {'generationConfig': {'temperature': .3}}}})
    assert p.get_messages()[0]['parts'] == [{'text': 'Actual system'}]
    p.chat()
    assert req[0]['generationConfig']['seed'] == 42
    assert req[0]['generationConfig']['temperature'] == .3
    p.cleanup()


def test_custom_contents_do_not_save_unsafe_replay_metadata(monkeypatch):
    contents = [{'role': 'user', 'parts': [{'text': 'Custom input'}]}]
    p, _, req, _ = make_provider(monkeypatch, [response([function_call()])], {
        'extra_body': {'contents': contents}})
    assert p.get_messages() == contents
    p.chat()
    assert req[0]['contents'] == contents
    assert p.get_assistant_metadata() == {}
    p.cleanup()


def test_explicit_native_settings_are_not_mutated(monkeypatch):
    params = {'thinking_config': {'thinking_level': 'low'},
              'extra_body': {'generationConfig': {'seed': 42}},
              'http_options': {'retry_options': {'attempts': 1}}}
    p, s, _, _ = make_provider(monkeypatch, [response()], params)
    before = deepcopy(s.params)
    p.chat()
    assert s.params == before
    p.cleanup()


def test_cost_cache_read_alias_and_default_rate(monkeypatch):
    usage = {'promptTokenCount': 100, 'cachedContentTokenCount': 80}
    p, _, _, _ = make_provider(monkeypatch, [response(usage=usage)], {
        'price_in': 2, 'price_cache_in': .2, 'price_cache_read': .1})
    p.chat()
    assert p.get_cost()['input_cost'] == .000048
    p.cleanup()


def test_usage_unknown_is_distinct_from_zero(monkeypatch):
    p, _, _, _ = make_provider(monkeypatch, [response(), response(usage={}),
                                          response(usage={'promptTokenCount': 0})])
    p.chat()
    assert not p.get_usage()['turn_usage_known']
    p.chat()
    assert not p.get_usage()['turn_usage_known']
    p.chat()
    assert p.get_usage()['turn_usage_known']
    assert p.get_usage()['turn_in'] == 0
    p.cleanup()


def test_request_duration_recorded_once(monkeypatch):
    wire = Wire([response([{'text': 'one'}], finish=None), response()])
    p, _, _, _ = make_provider(monkeypatch, [wire])
    times = iter([10.0, 13.0])
    monkeypatch.setattr('providers.google_provider.monotonic', lambda: next(times))
    list(p.stream_chat())
    assert p.get_usage()['total_time'] == p.get_usage()['turn_time'] == 3.0
    p.cleanup()


@pytest.mark.parametrize('arguments', [None, {}])
def test_missing_required_arguments_do_not_execute(monkeypatch, arguments):
    call = function_call()
    call['functionCall']['args'] = arguments
    p, s, _, _ = make_provider(monkeypatch, [response([call])])
    out = p.chat()
    runner = TurnRunner(s)
    runner._record_assistant(out)
    runner._execute_tools(out)
    assert s.executed == []
    assert p.get_assistant_metadata() == {}
    p.cleanup()


def test_orphaned_tool_result_survives_history_trimming(monkeypatch):
    p, s, req, _ = make_provider(monkeypatch, [response()])
    s.chat.conversation.clear()
    s.chat.add('Earlier result', role='tool', extra={'tool_call_id': 'missing_call'})
    assert p.chat() == 'Done'
    assert 'Historical tool result' in req[0]['contents'][0]['parts'][0]['text']
    assert 'Earlier result' in req[0]['contents'][0]['parts'][0]['text']
    p.cleanup()


def test_new_requests_use_new_rates_without_repricing_old_requests(monkeypatch):
    r = response(usage={'promptTokenCount': 100, 'candidatesTokenCount': 10})
    p, s, _, _ = make_provider(monkeypatch, [r, r], {'price_in': 2, 'price_out': 12})
    p.chat()
    first = p.get_cost()['total_cost']
    s.params.update(price_in=4, price_out=24)
    p.chat()
    assert p.get_cost()['total_cost'] == round(first * 3, 6)
    assert p.get_usage()['total_in'] == 200
    p.cleanup()


def test_unnamed_function_call_cannot_be_replayed(monkeypatch):
    call = function_call()
    call['functionCall']['name'] = ''
    p, _, _, _ = make_provider(monkeypatch, [response([call])])
    p.chat()
    assert p.get_finish_reason() == 'error'
    assert p.get_tool_calls() == []
    assert p.get_assistant_metadata() == {}
    p.cleanup()


def test_cached_tool_call_still_validates_local_required_arguments(monkeypatch):
    call = function_call()
    call['functionCall']['args'] = None
    p, s, _, _ = make_provider(monkeypatch, [response([call])], {
        'cached_content': 'cachedContents/example'})
    runner = TurnRunner(s)
    out = p.chat()
    runner._record_assistant(out)
    runner._execute_tools(out)
    assert s.executed == []
    assert p.get_assistant_metadata() == {}
    p.cleanup()
