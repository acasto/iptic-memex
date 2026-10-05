"""Anthropic wire contracts through the real SDK and an offline HTTP transport."""

import json
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from anthropic import Anthropic

from contexts.chat_context import ChatContext
from core.cancellation import CancellationToken
from core.turns import TurnRunner
from providers.anthropic_provider import AnthropicProvider


class Session:
    def __init__(self, params=None, tools=False, mcp=False):
        self.params = {'model_name': 'offline-model', 'max_tokens': 100, **(params or {})}
        self.chat = ChatContext(self)
        self.chat.add('Please echo hello')
        self.prompt = SimpleNamespace(get=lambda: {'content': 'System instructions'})
        self.data = {'__tool_api_to_cmd__': {'echo_api': 'mcp:demo.echo'}}
        self.flags = {}
        self.executed = []
        self.tools, self.mcp = tools, mcp
        self.token = None
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
        return {'chat': self.chat, 'prompt': self.prompt}.get(name)

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
        return self.mcp if (section, key) == ('MCP', 'active') else fallback

    def get_user_data(self, name, default=None):
        return self.data.get(name, default)

    def get_provider(self):
        return self.provider

    def get_flag(self, name):
        return self.flags.get(name)

    def set_flag(self, name, value):
        self.flags[name] = value

    def get_cancellation_token(self):
        return self.token


def text(value='Done'):
    return {'type': 'text', 'text': value}


def tool(arguments=None, ident='toolu_1', name='echo_api'):
    return {'type': 'tool_use', 'id': ident, 'name': name,
            'input': {'value': 'hello'} if arguments is None else arguments}


def message(content=None, reason='end_turn', usage=None, **extra):
    return {'id': 'msg_offline', 'type': 'message', 'role': 'assistant',
            'model': 'offline-model', 'content': content if content is not None else [text()],
            'stop_reason': reason, 'stop_sequence': None,
            'usage': usage if usage is not None else {'input_tokens': 10, 'output_tokens': 5},
            **extra}


def wire(events):
    return ''.join(f'event: {event["type"]}\ndata: {json.dumps(event)}\n\n' for event in events)


def events(content=None, reason='end_turn', usage=None, terminal=True, arguments=None):
    result = [{'type': 'message_start', 'message': message([], reason=None)}]
    for index, block in enumerate(content if content is not None else [text()]):
        start = deepcopy(block)
        kind = block['type']
        if kind == 'text':
            start['text'] = ''
            deltas = [{'type': 'text_delta', 'text': block['text']}]
        elif kind == 'thinking':
            start.update(thinking='', signature='')
            deltas = [{'type': 'thinking_delta', 'thinking': block['thinking']},
                      {'type': 'signature_delta', 'signature': block['signature']}]
        elif kind == 'tool_use':
            start['input'] = {}
            raw = json.dumps(block['input']) if arguments is None else arguments
            split = len(raw) // 2
            deltas = [{'type': 'input_json_delta', 'partial_json': part}
                      for part in (raw[:split], raw[split:])]
        else:
            deltas = []
        result.append({'type': 'content_block_start', 'index': index, 'content_block': start})
        result.extend({'type': 'content_block_delta', 'index': index, 'delta': delta}
                      for delta in deltas)
        result.append({'type': 'content_block_stop', 'index': index})
    if terminal:
        result.extend([{'type': 'message_delta',
                        'delta': {'stop_reason': reason, 'stop_sequence': None},
                        'usage': usage or {'output_tokens': 20}}, {'type': 'message_stop'}])
    return result


def make_provider(monkeypatch, session, replies):
    requests = []
    iterator = iter(replies)

    def handle(request):
        requests.append({'body': json.loads(request.content), 'headers': dict(request.headers)})
        reply = next(iterator)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str):
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=reply)
        return httpx.Response(200, json=reply)

    client = Anthropic(api_key='offline-test', base_url='http://offline.test', max_retries=0,
                       http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(AnthropicProvider, '_initialize_client', lambda self: client)
    provider = AnthropicProvider(session)
    session.provider = provider
    return provider, requests


@pytest.mark.parametrize('stream', [False, True])
def test_all_text_and_signed_thinking_round_trip(monkeypatch, stream):
    session = Session({'stream': stream, 'prompt_caching': True}, tools=True)
    content = [{'type': 'thinking', 'thinking': 'Summary', 'signature': 'opaque-signature'},
               {'type': 'redacted_thinking', 'data': 'opaque-redacted'},
               text('First'), text('Second'), tool()]
    replies = [wire(events(content, reason='tool_use')) if stream else message(content, 'tool_use'),
               wire(events()) if stream else message()]
    provider, requests = make_provider(monkeypatch, session, replies)
    output = ''.join(provider.stream_chat()) if stream else provider.chat()
    assert output == 'FirstSecond'
    assert provider.get_current_reasoning() == 'Summary'
    runner = TurnRunner(session)
    runner._record_assistant(output)
    original_meta = deepcopy(session.chat.get()[1]['anthropic_content'])
    assert original_meta == content
    json.dumps(session.chat.get()[1])  # Checkpoint serialization must be possible.
    assert runner._execute_tools(output)
    assert session.executed == [{'value': 'hello'}]
    assert provider.get_tool_calls() == []
    # Introspection must not change the prefix attached to subsequent metadata.
    assert provider.get_messages()[2]['content'] == content
    answer = ''.join(provider.stream_chat()) if stream else provider.chat()
    assert answer == 'Done'
    followup = requests[1]['body']['messages']
    assert followup[1]['content'] == content
    assert followup[2]['content'][0]['tool_use_id'] == 'toolu_1'
    assert followup[2]['content'][0]['cache_control'] == {'type': 'ephemeral'}
    assert session.chat.get()[1]['anthropic_content'] == original_meta
    raw = provider.get_full_response()
    assert (raw['stop_reason'] if stream else raw.stop_reason) == 'end_turn'
    provider.cleanup()


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('arguments', ['{', '[]', 'null', '"text"'])
def test_invalid_arguments_cannot_execute(monkeypatch, stream, arguments):
    session = Session({'stream': stream}, tools=True)
    reply = wire(events([tool()], 'tool_use', arguments=arguments)) if stream else message(
        [tool(json.loads(arguments) if arguments != '{' else arguments)], 'tool_use')
    # null is deliberately kept distinct from the helper's default input.
    if not stream and arguments == 'null':
        reply['content'][0]['input'] = None
    provider, _ = make_provider(monkeypatch, session, [reply])
    output = ''.join(provider.stream_chat()) if stream else provider.chat()
    runner = TurnRunner(session)
    runner._record_assistant(output)
    runner._execute_tools(output)
    assert session.executed == []
    assert provider.get_assistant_metadata() == {}
    provider.cleanup()


@pytest.mark.parametrize('reason', ['max_tokens', 'model_context_window_exceeded', 'refusal'])
@pytest.mark.parametrize('stream', [False, True])
def test_terminal_outcomes_skip_tools(monkeypatch, reason, stream):
    session = Session({'stream': stream}, tools=True)
    reply = wire(events([tool()], reason)) if stream else message([tool()], reason)
    provider, _ = make_provider(monkeypatch, session, [reply])
    output = ''.join(provider.stream_chat()) if stream else provider.chat()
    assert provider.get_finish_reason() == ('content_filter' if reason == 'refusal' else 'length')
    runner = TurnRunner(session)
    runner._record_assistant(output)
    runner._execute_tools(output)
    assert session.executed == []
    assert provider.get_usage()['turn_out'] > 0
    provider.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_failed_request_does_not_reexecute_previous_tools(monkeypatch, stream):
    session = Session({'stream': stream}, tools=True)
    success = wire(events([text('Calling'), tool()], 'tool_use')) if stream else message(
        [text('Calling'), tool()], 'tool_use')
    provider, _ = make_provider(monkeypatch, session, [success, httpx.ReadError('Offline failure')])
    if stream:
        list(provider.stream_chat())
        output = ''.join(provider.stream_chat())
    else:
        provider.chat()
        output = provider.chat()
    assert 'Connection error' in output
    assert provider.get_finish_reason() == 'error'
    assert provider.get_full_response() is None
    assert provider.get_usage()['turn_in'] == 0
    assert not TurnRunner(session)._execute_tools(output)
    assert session.executed == []
    assert provider.get_tool_calls() == []
    provider.cleanup()


def test_stream_to_text_and_reset_clear_unconsumed_calls(monkeypatch):
    session = Session({'stream': True}, tools=True)
    provider, _ = make_provider(monkeypatch, session,
                                [wire(events([tool()], 'tool_use')), message(),
                                 message([tool()], 'tool_use')])
    list(provider.stream_chat())
    session.params['stream'] = False
    assert provider.chat() == 'Done'
    assert provider.get_tool_calls() == []
    provider.chat()
    provider.reset_usage()
    assert provider.get_tool_calls() == []
    assert provider.get_full_response() is None
    provider.cleanup()


@pytest.mark.parametrize('mutation', ['user', 'assistant', 'trim', 'model', 'system', 'tools', 'args'])
def test_modified_history_does_not_replay_signed_blocks(monkeypatch, mutation):
    session = Session(tools=True)
    content = [{'type': 'thinking', 'thinking': 'Summary', 'signature': 'opaque'}, text(), tool()]
    provider, requests = make_provider(monkeypatch, session,
                                       [message(content, 'tool_use'), message()])
    runner = TurnRunner(session)
    output = provider.chat()
    runner._record_assistant(output)
    runner._execute_tools(output)
    if mutation == 'user':
        session.chat.get()[0]['message'] = 'Edited'
    elif mutation == 'assistant':
        session.chat.get()[1]['message'] = 'Edited'
    elif mutation == 'trim':
        session.chat.get().pop(0)
    elif mutation == 'model':
        session.params['model_name'] = 'another-model'
    elif mutation == 'system':
        session.prompt = SimpleNamespace(get=lambda: {'content': 'Different instructions'})
    elif mutation == 'tools':
        session.params['tools'] = [{'name': 'extra', 'input_schema': {'type': 'object'}}]
    else:
        session.chat.get()[1]['tool_calls'][0]['arguments'] = {'value': 'changed'}
    provider.chat()
    assert all(b['type'] != 'thinking' for m in requests[1]['body']['messages'] for b in m['content'])
    provider.cleanup()


def test_checkpoint_reload_replays_native_blocks(monkeypatch):
    session = Session(tools=True)
    content = [{'type': 'thinking', 'thinking': '', 'signature': 'opaque'}, text(), tool()]
    provider, _ = make_provider(monkeypatch, session, [message(content, 'tool_use')])
    runner = TurnRunner(session)
    output = provider.chat()
    runner._record_assistant(output)
    runner._execute_tools(output)
    saved = json.loads(json.dumps(session.chat.get()))
    stats = provider.get_usage()
    provider.cleanup()
    restored = Session(tools=True)
    restored.chat.conversation = saved
    provider, requests = make_provider(monkeypatch, restored, [message()])
    provider.set_usage(stats)
    provider.chat()
    assert requests[0]['body']['messages'][1]['content'] == content
    assert provider.get_usage()['total_out'] == 10
    provider.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_parallel_tool_results_are_grouped(monkeypatch, stream):
    session = Session({'stream': stream}, tools=True)
    content = [text('Calling'), tool(), tool(ident='toolu_2')]
    reply = wire(events(content, 'tool_use')) if stream else message(content, 'tool_use')
    provider, requests = make_provider(monkeypatch, session, [reply, message()])
    output = ''.join(provider.stream_chat()) if stream else provider.chat()
    runner = TurnRunner(session)
    runner._record_assistant(output)
    runner._execute_tools(output)
    session.params['stream'] = False
    provider.chat()
    results = requests[1]['body']['messages'][2]['content']
    assert [b['tool_use_id'] for b in results] == ['toolu_1', 'toolu_2']
    assert len(requests[1]['body']['messages']) == 3
    provider.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_pause_turn_preserves_server_blocks_and_accumulates_usage(monkeypatch, stream):
    session = Session({'stream': stream})
    content = [text('Searching'), {'type': 'server_tool_use', 'id': 'srv_1',
                                  'name': 'web_search', 'input': {'query': 'hello'}}]
    replies = [wire(events(content, 'pause_turn')) if stream else message(content, 'pause_turn'),
               wire(events()) if stream else message()]
    provider, requests = make_provider(monkeypatch, session, replies)
    output = ''.join(provider.stream_chat()) if stream else provider.chat()
    assert output == 'SearchingDone'
    assert len(requests) == 2
    assert requests[1]['body']['messages'][1]['content'] == content
    assert provider.get_finish_reason() == 'end_turn'
    assert provider.get_usage()['total_out'] == (40 if stream else 10)
    assert provider.get_assistant_metadata()['anthropic_content'] == [*content, text()]
    provider.cleanup()


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('limit', [0, 1])
def test_pause_turn_continuations_are_bounded(monkeypatch, stream, limit):
    session = Session({'stream': stream, 'max_server_continuations': limit})
    reply = (wire(events([text('Wait')], 'pause_turn')) if stream
             else message([text('Wait')], 'pause_turn'))
    provider, requests = make_provider(monkeypatch, session, [reply, reply])
    output = ''.join(provider.stream_chat()) if stream else provider.chat()
    assert 'continuation limit' in output
    assert len(requests) == limit + 1
    assert provider.get_finish_reason() == 'pause_turn'
    provider.cleanup()


@pytest.mark.parametrize('terminal', [False, True])
def test_unfinished_stream_and_error_preserve_observed_usage(monkeypatch, terminal):
    session = Session({'stream': True}, tools=True)
    stream = events([text('Partial'), tool()], 'tool_use', terminal=False)
    if terminal:
        stream.append({'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Offline'}})
    provider, _ = make_provider(monkeypatch, session, [wire(stream)])
    output = ''.join(provider.stream_chat())
    assert output.startswith('Partial')
    assert provider.get_usage()['turn_in'] == 10
    assert provider.get_finish_reason() == ('error' if terminal else 'length')
    for call in provider.get_tool_calls():
        assert call['truncated']
    assert provider.get_assistant_metadata() == {}
    provider.cleanup()


class TrackingStream(httpx.SyncByteStream):
    def __init__(self, content):
        self.content, self.closed = content.encode(), False

    def __iter__(self):
        yield self.content

    def close(self):
        self.closed = True


@pytest.mark.parametrize('cancel', [False, True])
def test_early_close_and_cancellation_close_upstream(monkeypatch, cancel):
    stream = TrackingStream(wire(events([text('First'), tool()], 'tool_use')))
    client = Anthropic(api_key='offline', max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(
            200, headers={'content-type': 'text/event-stream'}, stream=stream))))
    monkeypatch.setattr(AnthropicProvider, '_initialize_client', lambda self: client)
    session = Session({'stream': True}, tools=True)
    session.token = CancellationToken()
    provider = AnthropicProvider(session)
    iterator = provider.stream_chat()
    assert next(iterator) == 'First'
    if cancel:
        session.token.cancel('test')
        list(iterator)
    else:
        iterator.close()
    assert stream.closed
    assert provider.get_finish_reason() == 'cancelled'
    calls = provider.get_tool_calls()
    assert all(c['truncated'] for c in calls)
    provider.cleanup()
    assert client.is_closed()


def test_final_cumulative_usage_cache_ttls_and_costs(monkeypatch):
    counts = {'input_tokens': 100, 'output_tokens': 20, 'cache_creation_input_tokens': 300,
              'cache_read_input_tokens': 600, 'cache_creation': {
                  'ephemeral_5m_input_tokens': 200, 'ephemeral_1h_input_tokens': 100}}
    session = Session({'stream': True, 'price_in': 3, 'price_out': 15,
                       'price_cache_write': 3.75, 'price_cache_read': .3,
                       'price_cache_write_1h': 6})
    provider, _ = make_provider(monkeypatch, session, [wire(events(usage=counts)), message()])
    assert ''.join(provider.stream_chat()) == 'Done'
    stats = provider.get_usage()
    assert stats['turn_in'] == 1000
    assert stats['turn_content_tokens'] == 1000
    assert stats['turn_total'] == 1020
    assert stats['total_time'] == stats['turn_time']
    assert provider.get_cost()['total_cost'] == .00213
    session.params.update(price_in=30, price_out=150, price_cache_write=37.5, price_cache_read=3)
    assert provider.get_cost()['total_cost'] == .00213
    session.params['stream'] = False
    provider.chat()
    assert provider.get_cost()['total_cost'] == .00318
    assert provider.get_usage()['total_in'] == 1010
    provider.cleanup()


@pytest.mark.parametrize('vision', [False, True])
def test_vision_gate_and_introspection(monkeypatch, vision):
    session = Session({'vision': vision})
    session.chat.get()[0]['context'] = [{'type': 'image', 'context': SimpleNamespace(
        get=lambda: {'mime_type': 'image/png', 'content': 'AAAA'})}]
    provider, requests = make_provider(monkeypatch, session, [message()])
    provider.chat()
    content = requests[0]['body']['messages'][0]['content']
    assert any(b['type'] == 'image' for b in content) == vision
    assert provider.get_messages()[1]['content'] == content
    provider.cleanup()


def test_native_parameters_custom_tools_and_exclusions(monkeypatch):
    params = {'thinking': {'type': 'adaptive'}, 'reasoning_effort': 'high',
              'output_config': {'format': {'type': 'json_schema', 'schema': {'type': 'object'}}},
              'tools': [{'type': 'web_search_20250305', 'name': 'web_search'}],
              'extra_body': '{"custom": true, "temperature": 0.8}',
              'temperature': .3, 'excluded_parameters': ['temperature'],
              'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}
    session = Session(params, tools=True)
    original = deepcopy(session.params)
    provider, requests = make_provider(monkeypatch, session, [message()])
    provider.chat()
    body = requests[0]['body']
    assert body['thinking'] == {'type': 'adaptive'}
    assert body['output_config']['effort'] == 'high'
    assert body['custom'] is True
    assert 'temperature' not in body
    assert [t['name'] for t in body['tools']] == ['web_search', 'echo_api']
    assert body['cache_control']['ttl'] == '1h'
    assert session.params == original
    provider.cleanup()


@pytest.mark.parametrize('strategy', ['explicit', 'automatic'])
def test_cache_strategy_and_exclusion(monkeypatch, strategy):
    session = Session({'prompt_caching': True, 'cache_strategy': strategy, 'prompt_cache_ttl': '1h'})
    provider, requests = make_provider(monkeypatch, session, [message(), message()])
    provider.chat()
    body = requests[0]['body']
    cache = (body['cache_control'] if strategy == 'automatic'
             else body['messages'][0]['content'][-1]['cache_control'])
    assert cache['ttl'] == '1h'
    if strategy == 'automatic':
        assert 'cache_control' not in body['system'][0]
    session.params['excluded_parameters'] = 'cache_control'
    provider.chat()
    assert 'cache_control' not in json.dumps(requests[1]['body'])
    provider.cleanup()


@pytest.mark.parametrize('legacy', [False, True])
def test_mcp_current_and_legacy_wire_shapes(monkeypatch, legacy):
    session = Session({'mcp_servers': 'demo=https://offline.example/sse',
                       'mcp_headers_demo': '{"Authorization":"Bearer offline-token"}',
                       'mcp_allowed_demo': ['echo'],
                       **({'mcp_beta': 'legacy'} if legacy else {})}, tools=True, mcp=True)
    provider, requests = make_provider(monkeypatch, session, [message()])
    assert provider.chat() == 'Done'
    body, headers = requests[0]['body'], requests[0]['headers']
    server = body['mcp_servers'][0]
    assert server['authorization_token'] == 'offline-token'
    assert headers['anthropic-beta'] == ('mcp-client-2025-04-04' if legacy else 'mcp-client-2025-11-20')
    if legacy:
        assert server['tool_configuration']['allowed_tools'] == ['echo']
    else:
        assert 'tool_configuration' not in server
        toolset = next(t for t in body['tools'] if t.get('type') == 'mcp_toolset')
        assert toolset['default_config'] == {'enabled': False}
        assert toolset['configs'] == {'echo': {'enabled': True}}
    provider.cleanup()


@pytest.mark.parametrize('escape', [False, True])
def test_inactive_mcp_cannot_leak_through_raw_configuration(monkeypatch, escape):
    params = {'mcp_servers': 'demo=https://offline.example/sse',
              'tools': [{'type': 'mcp_toolset', 'mcp_server_name': 'demo'}]}
    session = Session({'extra_body': params} if escape else params, mcp=False)
    provider, requests = make_provider(monkeypatch, session, [message()])
    provider.chat()
    assert 'mcp_servers' not in requests[0]['body']
    assert not requests[0]['body'].get('tools')
    assert 'anthropic-beta' not in requests[0]['headers']
    provider.cleanup()


def test_older_sdk_and_missing_beta_surface_keep_wire_fields(monkeypatch):
    session = Session({'thinking': {'type': 'adaptive'}, 'output_config': {'effort': 'high'},
                       'mcp_servers': 'demo=https://offline.example/sse'}, mcp=True)
    provider, requests = make_provider(monkeypatch, session, [message()])
    real = provider.client.messages.create

    def old_create(*, model, messages, max_tokens, system=None, tools=None, stream=False,
                   extra_body=None, extra_headers=None):
        return real(model=model, messages=messages, max_tokens=max_tokens,
                    system=system, tools=tools, stream=stream,
                    extra_body=extra_body, extra_headers=extra_headers)

    # Proxy an old SDK without its beta namespace, using real SDK serialization underneath.
    provider.client = SimpleNamespace(messages=SimpleNamespace(create=old_create),
                                      close=provider.client.close)
    assert provider.chat() == 'Done'
    body = requests[0]['body']
    assert body['thinking'] == {'type': 'adaptive'}
    assert body['output_config'] == {'effort': 'high'}
    assert body['mcp_servers'][0]['name'] == 'demo'
    assert requests[0]['headers']['anthropic-beta'] == 'mcp-client-2025-11-20'
    assert 'betas' not in body
    provider.cleanup()


def test_introspection_before_recording_does_not_change_replay_metadata(monkeypatch):
    session = Session(tools=True)
    content = [{'type': 'thinking', 'thinking': 'Summary', 'signature': 'opaque'}, text(), tool()]
    provider, requests = make_provider(monkeypatch, session,
                                       [message(content, 'tool_use'), message()])
    output = provider.chat()
    provider.get_messages()
    runner = TurnRunner(session)
    runner._record_assistant(output)
    runner._execute_tools(output)
    provider.chat()
    assert requests[1]['body']['messages'][1]['content'] == content
    provider.cleanup()


def test_stream_preserves_native_server_usage_and_citations(monkeypatch):
    session = Session({'stream': True})
    stream = events(usage={'input_tokens': 10, 'output_tokens': 20,
                           'server_tool_use': {'web_search_requests': 2}})
    citation = {'type': 'char_location', 'cited_text': 'abc', 'document_index': 0,
                'document_title': 'Source', 'start_char_index': 0, 'end_char_index': 3}
    stream.insert(3, {'type': 'content_block_delta', 'index': 0,
                      'delta': {'type': 'citations_delta', 'citation': citation}})
    provider, _ = make_provider(monkeypatch, session, [wire(stream)])
    assert ''.join(provider.stream_chat()) == 'Done'
    raw = provider.get_full_response()
    assert raw['usage']['server_tool_use'] == {'web_search_requests': 2}
    assert raw['content'][0]['citations'] == [citation]
    provider.cleanup()


@pytest.mark.parametrize('stream', [False, True])
def test_mcp_native_results_survive_tool_turn(monkeypatch, stream):
    session = Session({'stream': stream, 'mcp_servers': 'demo=https://offline.example/sse'},
                      tools=True, mcp=True)
    content = [
        {'type': 'mcp_tool_use', 'id': 'mcpu_1', 'name': 'search',
         'server_name': 'demo', 'input': {'query': 'hello'}},
        {'type': 'mcp_tool_result', 'tool_use_id': 'mcpu_1', 'is_error': False,
         'content': [text('Found')]},
        text('Now echo'), tool(),
    ]
    reply = wire(events(content, 'tool_use')) if stream else message(content, 'tool_use')
    provider, requests = make_provider(monkeypatch, session, [reply, message()])
    output = ''.join(provider.stream_chat()) if stream else provider.chat()
    assert output == 'Now echo'
    runner = TurnRunner(session)
    runner._record_assistant(output)
    assert runner._execute_tools(output)
    assert session.executed == [{'value': 'hello'}]
    session.params['stream'] = False
    provider.chat()
    assert requests[1]['body']['messages'][1]['content'] == content
    provider.cleanup()


@pytest.mark.parametrize('beta_surface', [False, True])
def test_older_sdk_without_betas_keyword_keeps_required_header(monkeypatch, beta_surface):
    session = Session({'mcp_servers': 'demo=https://offline.example/sse',
                       'betas': ['extra-beta'], 'extra_headers': {'anthropic-beta': 'header-beta'}},
                      mcp=True)
    provider, requests = make_provider(monkeypatch, session, [message()])
    real = provider.client.messages.create

    def old_create(*, model, messages, max_tokens, system=None, tools=None, stream=False,
                   extra_body=None, extra_headers=None):
        return real(model=model, messages=messages, max_tokens=max_tokens,
                    system=system, tools=tools, stream=stream,
                    extra_body=extra_body, extra_headers=extra_headers)

    surface = SimpleNamespace(create=old_create)
    close = provider.client.close
    provider.client = SimpleNamespace(messages=surface, close=close)
    if beta_surface:
        provider.client.beta = SimpleNamespace(messages=surface)
    assert provider.chat() == 'Done'
    assert requests[0]['headers']['anthropic-beta'] == 'extra-beta,mcp-client-2025-11-20,header-beta'
    assert 'betas' not in requests[0]['body']
    provider.cleanup()


def test_alias_precedence_and_json_tools_config(monkeypatch):
    session = Session({'thinking': {'type': 'disabled'}, 'thinking_budget': 2048,
                       'output_config': {'effort': 'low'}, 'reasoning_effort': 'high',
                       'tools': '[{"type":"web_search_20250305","name":"web_search","strict":true}]'})
    provider, requests = make_provider(monkeypatch, session, [message()])
    provider.chat()
    body = requests[0]['body']
    assert body['thinking'] == {'type': 'disabled'}
    assert body['output_config'] == {'effort': 'low'}
    assert body['tools'][0]['strict'] is True
    provider.cleanup()


def test_excluding_cache_controls_does_not_edit_tool_input(monkeypatch):
    session = Session({'prompt_caching': True, 'excluded_parameters': 'cache_control'}, tools=True)
    content = [text(), tool({'cache_control': 'a business argument'})]
    provider, requests = make_provider(monkeypatch, session,
                                       [message(content, 'tool_use'), message()])
    runner = TurnRunner(session)
    output = provider.chat()
    runner._record_assistant(output)
    runner._execute_tools(output)
    provider.chat()
    assert requests[1]['body']['messages'][1]['content'][1]['input'] == {
        'cache_control': 'a business argument'}
    provider.cleanup()


def test_cache_cost_totals_restore_without_repricing(monkeypatch):
    session = Session({'price_in': 3, 'price_out': 15, 'price_cache_in': 3.75, 'price_cache_out': .3})
    counts = {'input_tokens': 100, 'output_tokens': 20,
              'cache_creation_input_tokens': 300, 'cache_read_input_tokens': 600}
    provider, _ = make_provider(monkeypatch, session, [message(usage=counts)])
    provider.chat()
    saved, costs = provider.get_usage(), provider.get_cost()
    assert costs['total_cost'] == .001905
    provider.cleanup()
    session.params.update(price_in=30, price_out=150, price_cache_in=37.5, price_cache_out=3)
    provider, _ = make_provider(monkeypatch, session, [message()])
    provider.set_usage(saved)
    assert provider.get_usage()['total_in'] == 1000
    assert provider.get_cost() == costs
    provider.chat()
    assert provider.get_cost()['total_cost'] == .002955
    provider.cleanup()


@pytest.mark.parametrize('reason', ['pause_turn', 'cancelled'])
def test_paused_or_cancelled_output_cannot_execute_pseudo_commands(monkeypatch, reason):
    session = Session()
    provider, _ = make_provider(monkeypatch, session, [message()])
    provider.chat()
    provider._last_finish_reason = reason
    commands = SimpleNamespace(parse_commands=lambda value: [{'command': 'echo'}],
                               run=lambda value: session.executed.append(value))
    session.get_action = lambda name: commands if name == 'assistant_commands' else None
    assert not TurnRunner(session)._execute_tools('%%CMD%% command="echo"\n%%END%%')
    assert session.executed == []
    provider.cleanup()


def test_escape_hatch_cache_control_suppresses_generated_breakpoints(monkeypatch):
    session = Session({'prompt_caching': True,
                       'extra_body': {'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}})
    provider, requests = make_provider(monkeypatch, session, [message()])
    provider.chat()
    body = requests[0]['body']
    assert body['cache_control']['ttl'] == '1h'
    assert 'cache_control' not in body['system'][0]
    assert 'cache_control' not in body['messages'][0]['content'][0]
    provider.cleanup()


def test_native_disabled_mcp_server_stays_disabled(monkeypatch):
    session = Session({'mcp_servers': [{'name': 'demo', 'type': 'url',
                                       'url': 'https://offline.example/sse',
                                       'tool_configuration': {'enabled': False,
                                                              'allowed_tools': ['echo']}}]}, mcp=True)
    provider, requests = make_provider(monkeypatch, session, [message()])
    provider.chat()
    toolset = requests[0]['body']['tools'][0]
    assert toolset['default_config'] == {'enabled': False}
    assert not toolset.get('configs')
    provider.cleanup()


def test_client_connection_settings_and_cleanup():
    session = Session({'api_key': 'offline', 'base_url': 'http://offline.test',
                       'timeout': 2, 'max_retries': 1,
                       'default_headers': '{"x-offline-test":"yes"}'})
    provider = AnthropicProvider(session)
    assert str(provider.client.base_url) == 'http://offline.test'
    assert provider.client.timeout == 2
    assert provider.client.max_retries == 1
    assert provider.client.default_headers['x-offline-test'] == 'yes'
    provider.cleanup()
    assert provider.client.is_closed()


@pytest.mark.parametrize('servers', [
    '{"demo":"https://offline.example/sse"}',
    '[{"type":"url","name":"demo","url":"https://offline.example/sse"}]',
])
def test_json_mcp_server_settings(monkeypatch, servers):
    session = Session({'mcp_servers': servers}, mcp=True)
    provider, requests = make_provider(monkeypatch, session, [message()])
    assert provider.chat() == 'Done'
    assert requests[0]['body']['mcp_servers'][0]['name'] == 'demo'
    provider.cleanup()


def test_legacy_tool_history_uses_current_api_name_mapping(monkeypatch):
    session = Session(tools=True)
    session.chat.get().extend([
        {'role': 'assistant', 'message': 'Calling', 'tool_calls': [
            {'id': 'toolu_1', 'name': 'mcp:demo.echo', 'arguments': {'value': 'hello'}}]},
        {'role': 'tool', 'tool_call_id': 'toolu_1', 'message': 'OK'},
    ])
    provider, requests = make_provider(monkeypatch, session, [message()])
    provider.chat()
    assert requests[0]['body']['messages'][1]['content'][1]['name'] == 'echo_api'
    provider.cleanup()


def test_unknown_costs_and_legacy_token_totals_survive_restore(monkeypatch):
    session = Session({'price_unit': 0})
    provider, _ = make_provider(monkeypatch, session, [message()])
    provider.chat()
    stats = provider.get_usage()
    assert provider.get_cost() is None
    provider.set_usage(stats)
    assert provider.get_cost() is None
    provider.set_usage({'total_in': 10, 'total_out': 5,
                        'total_cache_writes': 20, 'total_cache_hits': 30})
    assert provider.get_usage()['total_in'] == 60
    assert provider.get_usage()['total_uncached_in'] == 10
    assert provider.get_cost() is None
    provider.cleanup()


def test_missing_stream_usage_remains_unknown(monkeypatch):
    session = Session({'stream': True})
    stream = events()
    stream[0]['message']['usage'] = None
    stream[-2]['usage'] = None
    provider, _ = make_provider(monkeypatch, session, [wire(stream)])
    assert ''.join(provider.stream_chat()) == 'Done'
    assert provider.get_full_response()['usage'] is None
    assert provider.get_usage()['turn_usage_known'] is False
    provider.cleanup()
