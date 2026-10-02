from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from providers.openai_provider import OpenAIProvider


class FakeSession:
    """Minimal session for constructing OpenAIProvider without network."""

    def __init__(self, params=None):
        self._params = params or {}

    def get_params(self):
        return dict(self._params)

    def get_context(self, name):
        return None

    def get_action(self, name):
        return None

    def get_effective_tool_mode(self):
        return 'none'

    def get_tools(self):
        return {}


def _make_provider(params=None):
    p = OpenAIProvider.__new__(OpenAIProvider)
    p.session = FakeSession(params)
    p.last_api_param = None
    p._last_response = None
    p._last_stream_tool_calls = None
    p._last_finish_reason = None
    p._last_reasoning = None
    p.turn_usage = None
    p.running_usage = {'total_in': 0, 'total_out': 0, 'total_time': 0.0}
    p.parameters = ['model', 'messages', 'stream', 'extra_body']
    return p


def _chunk(delta=None, finish_reason=None, usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish_reason, delta=delta or SimpleNamespace())],
        usage=usage,
    )


def _tc_delta(index, name=None, args=None, id=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=args))


class _StubCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    def create(self, **kwargs):
        if 'stream' in kwargs and kwargs['stream'] is True:
            return _StubStream(self._chunks)
        return self._chunks[0]


class _StubStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __iter__(self):
        return iter(self._chunks)


class _StubClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(completions=_StubCompletions(chunks))


def test_streamed_partial_json_args_marked_truncated():
    """Arguments cut off mid-write must be flagged, not silently {}."""
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': True})
    chunks = [
        _chunk(delta=SimpleNamespace(
            tool_calls=[_tc_delta(0, name='cmd', args='{"pa', id='tc_1')]
        )),
        _chunk(delta=None, finish_reason='length'),
    ]
    p.client = _StubClient(chunks)
    list(p.stream_chat())  # consume

    calls = p.get_tool_calls()
    assert len(calls) == 1
    assert calls[0]['truncated'] is True
    assert calls[0]['arguments'] == {}
    assert p.get_finish_reason() == 'length'


def test_streamed_empty_json_args_not_truncated():
    """A parsed empty object is a legitimate argument set, not a truncation."""
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': True})
    chunks = [
        _chunk(delta=SimpleNamespace(
            tool_calls=[_tc_delta(0, name='cmd', args='{}', id='tc_1')]
        )),
        _chunk(delta=None, finish_reason='stop'),
    ]
    p.client = _StubClient(chunks)
    list(p.stream_chat())

    calls = p.get_tool_calls()
    assert len(calls) == 1
    assert calls[0]['truncated'] is False
    assert calls[0]['arguments'] == {}


def test_streamed_valid_args_not_truncated():
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': True})
    chunks = [
        _chunk(delta=SimpleNamespace(
            tool_calls=[_tc_delta(0, name='cmd', args='{"path": "a.txt"}', id='tc_1')]
        )),
        _chunk(delta=None, finish_reason='tool_calls'),
    ]
    p.client = _StubClient(chunks)
    list(p.stream_chat())

    calls = p.get_tool_calls()
    assert len(calls) == 1
    assert calls[0]['truncated'] is False
    assert calls[0]['arguments'] == {'path': 'a.txt'}


def test_nonstream_captures_finish_reason_and_reasoning():
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': False})
    resp = SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason='length',
            message=SimpleNamespace(content='partial', reasoning_content='thinking...'),
        )],
        usage=None,
    )
    p.client = _StubClient([resp])
    out = p.chat()
    assert out == 'partial'
    assert p.get_finish_reason() == 'length'
    assert p.get_current_reasoning() == 'thinking...'


def test_streamed_non_object_json_marked_truncated():
    """Valid JSON that is not an object (list/scalar) must be rejected."""
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': True})
    chunks = [
        _chunk(delta=SimpleNamespace(
            tool_calls=[_tc_delta(0, name='cmd', args='[]', id='tc_1')]
        )),
        _chunk(delta=None, finish_reason='tool_calls'),
    ]
    p.client = _StubClient(chunks)
    list(p.stream_chat())

    calls = p.get_tool_calls()
    assert len(calls) == 1
    assert calls[0]['truncated'] is True
    assert calls[0]['arguments'] == {}


def _make_nonstream_response(tool_call):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            finish_reason='length',
            message=SimpleNamespace(content='', tool_calls=[tool_call]),
        )],
        usage=None,
    )


def test_nonstream_partial_json_args_marked_truncated():
    """Non-streaming malformed arguments must be flagged (default agent mode)."""
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': False})
    tc = SimpleNamespace(id='tc_1', function=SimpleNamespace(name='cmd', arguments='{"path":'))
    p._last_response = _make_nonstream_response(tc)
    calls = p.get_tool_calls()
    assert len(calls) == 1
    assert calls[0]['truncated'] is True
    assert calls[0]['arguments'] == {}


def test_nonstream_non_object_json_marked_truncated():
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': False})
    tc = SimpleNamespace(id='tc_1', function=SimpleNamespace(name='cmd', arguments='[1,2]'))
    p._last_response = _make_nonstream_response(tc)
    calls = p.get_tool_calls()
    assert calls[0]['truncated'] is True
    assert calls[0]['arguments'] == {}


def test_nonstream_valid_and_empty_args_not_truncated():
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': False})
    tc_valid = SimpleNamespace(id='tc_1', function=SimpleNamespace(name='cmd', arguments='{"path": "a"}'))
    tc_empty = SimpleNamespace(id='tc_2', function=SimpleNamespace(name='cmd', arguments='{}'))
    p._last_response = _make_nonstream_response(tc_valid)
    calls = p.get_tool_calls()
    assert calls[0]['truncated'] is False
    assert calls[0]['arguments'] == {'path': 'a'}

    p2 = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': False})
    p2._last_response = _make_nonstream_response(tc_empty)
    calls = p2.get_tool_calls()
    assert calls[0]['truncated'] is False
    assert calls[0]['arguments'] == {}


def test_nonstream_missing_arguments_rejected():
    """arguments=None must be rejected, matching the streaming path."""
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': False})
    tc = SimpleNamespace(id='tc_1', function=SimpleNamespace(name='cmd', arguments=None))
    p._last_response = _make_nonstream_response(tc)
    calls = p.get_tool_calls()
    assert calls[0]['truncated'] is True
    assert calls[0]['arguments'] == {}


def test_nonstream_absent_arguments_rejected():
    """A tool call with no arguments field at all must be rejected."""
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': False})
    fn = SimpleNamespace(name='cmd')
    tc = SimpleNamespace(id='tc_1', function=fn)
    p._last_response = _make_nonstream_response(tc)
    calls = p.get_tool_calls()
    assert calls[0]['truncated'] is True
    assert calls[0]['arguments'] == {}


def test_nonstream_explicit_empty_object_valid():
    """Explicit {} and JSON '{}' remain legitimate empty argument sets."""
    for args in ({}, '{}'):
        p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': False})
        tc = SimpleNamespace(id='tc_1', function=SimpleNamespace(name='cmd', arguments=args))
        p._last_response = _make_nonstream_response(tc)
        calls = p.get_tool_calls()
        assert calls[0]['truncated'] is False, f"explicit empty {args!r} must stay valid"
        assert calls[0]['arguments'] == {}


def test_streamed_reasoning_content_captured():
    p = _make_provider({'provider': 'DGXSpark', 'model_name': 'm', 'stream': True})
    chunks = [
        _chunk(delta=SimpleNamespace(reasoning_content='step 1 ')),
        _chunk(delta=SimpleNamespace(reasoning_content='step 2')),
        _chunk(delta=SimpleNamespace(content='answer'), finish_reason='stop'),
    ]
    p.client = _StubClient(chunks)
    out = list(p.stream_chat())
    assert ''.join(out) == 'answer'
    assert p.get_current_reasoning() == 'step 1 step 2'
