from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import types
from providers.google_provider import GoogleProvider


class FakeSession:
    def __init__(self, mode='official'):
        self._params = {
            'model': 'gemini-2.5-flash',
            'stream': False,
        }
        self._mode = mode
        self._actions = {
            'assistant_commands': types.SimpleNamespace(commands={
                'CMD': {'args': ['command', 'arguments'], 'function': {'name': 'assistant_cmd_tool'}},
            })
        }
    def get_params(self):
        return dict(self._params)
    def get_action(self, name):
        return self._actions.get(name)
    def get_effective_tool_mode(self):
        return self._mode
    def get_context(self, kind):
        # Return minimal prompt/chat contexts for assemble_message
        if kind == 'prompt':
            return types.SimpleNamespace(get=lambda: {'content': 'sys'})
        if kind == 'chat':
            class Chat:
                def get(self):
                    return [{'role': 'user', 'message': 'hi', 'context': []}]
            return Chat()
        return None


def test_google_get_tool_calls_parses_candidates_function_call():
    sess = FakeSession('official')
    gp = GoogleProvider(sess)

    # Fake response object with candidates -> content.parts.function_call
    fc = types.SimpleNamespace(name='CMD', args={'command': 'echo', 'arguments': 'hello'})
    part = types.SimpleNamespace(function_call=fc)
    content = types.SimpleNamespace(parts=[part])
    cand = types.SimpleNamespace(content=content)
    resp = types.SimpleNamespace(candidates=[cand])
    gp._last_response = resp

    calls = gp.get_tool_calls()
    assert isinstance(calls, list) and len(calls) == 1
    assert calls[0]['name'] == 'cmd'
    assert calls[0]['arguments'].get('command') == 'echo'

