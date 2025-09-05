from __future__ import annotations

import os
import tempfile


class DummyFsHandler:
    def __init__(self, base):
        self.base = base
    def resolve_path(self, p, must_exist=True):
        if not os.path.isabs(p):
            p = os.path.join(self.base, p)
        return p if (not must_exist or os.path.exists(p)) else None
    def read_file(self, p, binary=False, encoding='utf-8'):
        rp = self.resolve_path(p)
        if rp is None:
            return None
        mode = 'rb' if binary else 'r'
        with open(rp, mode) as f:
            return f.read()


class DummyUtils:
    class Out:
        def write(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
        def stop_spinner(self): pass
    def __init__(self):
        self.output = DummyUtils.Out()
        self.fs = self
    # fallback read used nowhere in this test
    def read_file(self, *a, **k): return None


class DummySession:
    def __init__(self, base):
        self._added = []
        self._base = base
        self._tools = {}
        self.utils = DummyUtils()
    def get_action(self, name: str):
        if name == 'assistant_fs_handler':
            return DummyFsHandler(self._base)
        # no helpers used for plain text
        return None
    def add_context(self, kind: str, data=None):
        self._added.append((kind, data))
        return data
    def get_agent_write_policy(self):
        return None
    def get_tools(self):
        return self._tools
    def get_params(self):
        return {'model': None}
    def get_option_from_model(self, *a, **k):
        return None
    @property
    def ui(self):
        class Noop:
            def emit(self, *a, **k): pass
        return Noop()


def test_assistant_file_tool_read_plain_text_adds_file_dict():
    # Arrange: make a temp file
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'a.txt')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('hello')
        sess = DummySession(td)
        from actions.assistant_file_tool_action import AssistantFileToolAction
        act = AssistantFileToolAction(sess)

        # Act
        res = act.start({'mode': 'read', 'file': p})

        # Assert: a 'file' context was added with dict content
        kinds = [k for (k, d) in sess._added]
        assert 'file' in kinds
        payloads = [d for (k, d) in sess._added if k == 'file']
        assert any(isinstance(x, dict) and x.get('name') == 'a.txt' and x.get('content') == 'hello' for x in payloads)

