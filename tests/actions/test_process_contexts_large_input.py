from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from actions.process_contexts_action import ProcessContextsAction


class FakeOut:
    def __init__(self):
        self.lines = []
    def write(self, *args, **kwargs):
        if args:
            s = str(args[0]) if args[0] is not None else ''
            if s is not None:
                self.lines.append(s)


class FakeUtils:
    def __init__(self):
        self.output = FakeOut()
        self.input = type('I', (), {'get_input': lambda *a, **k: ''})()


class SimpleContext:
    def __init__(self, name: str, content: str):
        self._d = {'name': name, 'content': content}
    def get(self):
        return self._d


class FakeCountTokens:
    def __init__(self, session):
        self.session = session
    def count_tiktoken(self, text: str) -> int:
        # A simplistic token heuristic sufficient for tests
        return max(1, len(text.split()))


class FakeSession:
    def __init__(self, tools):
        self.utils = FakeUtils()
        self._tools = dict(tools)
        self._flags = {'auto_submit': True}
        self.context = {'file': [SimpleContext('big.txt', 'tok ' * 9000)]}
        # Defer creating actions until get_action is first called to avoid ctor ordering issues
        self._actions = {}
        self._assistant_added = []
    def get_tools(self):
        return dict(self._tools)
    def set_flag(self, name, value):
        self._flags[name] = value
    def get_flag(self, name, default=False):
        return self._flags.get(name, default)
    def get_action(self, name):
        if not self._actions:
            # Initialize both actions lazily
            self._actions['count_tokens'] = FakeCountTokens(self)
            self._actions['process_contexts'] = ProcessContextsAction(self)
        return self._actions.get(name)
    def add_context(self, kind: str, data):
        if kind == 'assistant':
            self._assistant_added.append(data)
    def get_params(self):
        return {}


def test_large_input_confirms_and_disables_auto_submit():
    sess = FakeSession({'large_input_limit': 100, 'confirm_large_input': True})
    act = ProcessContextsAction(sess)
    _ = act.process_contexts_for_user(auto_submit=True)
    # Gate should disable auto_submit and print a warning
    assert sess.get_flag('auto_submit') is False
    joined = '\n'.join(sess.utils.output.lines)
    assert 'exceed limit' in joined


def test_large_input_feedback_without_confirm():
    sess = FakeSession({'large_input_limit': 100, 'confirm_large_input': False})
    act = ProcessContextsAction(sess)
    _ = act.process_contexts_for_user(auto_submit=True)
    # Auto-submit should remain True, but assistant feedback should be added
    assert sess.get_flag('auto_submit') is True
    assert any((d.get('name') == 'assistant_feedback') for d in sess._assistant_added)
