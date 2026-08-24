from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.load_multiline_action import LoadMultilineAction


class DummyTabCompletion:
    def set_session(self, _session):
        pass

    def deactivate_completion(self):
        pass

    def run(self, _mode):
        pass


class DummyInput:
    def __init__(self, values):
        self.values = iter(values)

    def get_input(self, **_kwargs):
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return value


class DummyUtils:
    def __init__(self, values):
        self.tab_completion = DummyTabCompletion()
        self.input = DummyInput(values)


class BlockingCapabilities:
    blocking = True


class DummyUI:
    capabilities = BlockingCapabilities()

    def __init__(self):
        self.events = []

    def emit(self, kind, data):
        self.events.append((kind, data))

    def ask_text(self, _prompt, *, default=None, **_kwargs):
        return default


class FakeSession:
    def __init__(self, values):
        self.ui = DummyUI()
        self.utils = DummyUtils(values)
        self.contexts = []

    def add_context(self, kind, data):
        self.contexts.append((kind, data))


def test_cli_multiline_uses_explicit_done_terminator():
    session = FakeSession(['first line', 'second line', '.done'])
    action = LoadMultilineAction(session)

    result = action.run()

    assert result.payload['saved'] is True
    assert session.contexts == [
        (
            'multiline_input',
            {'name': 'Multiline Input', 'content': 'first line\nsecond line'},
        )
    ]
    assert any('.done' in data['message'] for kind, data in session.ui.events if kind == 'status')


def test_cli_multiline_keeps_ctrl_c_as_compatible_fallback():
    session = FakeSession(['first line', KeyboardInterrupt()])
    action = LoadMultilineAction(session)

    result = action.run()

    assert result.payload['saved'] is True
    assert session.contexts[0][1]['content'] == 'first line'
