from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modes.completion_mode import CompletionMode


class FakeOutput:
    def __init__(self): self.lines = []
    def write(self, m: str = "", end: str = "\n", **k): self.lines.append((str(m), end))


class FakeInputUtils:
    def __init__(self): self.output = FakeOutput()


class ChatContext:
    def __init__(self): self._msgs = []
    def add(self, content, role, contexts=None): self._msgs.append({"role": role, "content": content})
    def get(self, kind): return list(self._msgs) if kind == 'all' else None


class StdinContext:
    def __init__(self, content: str): self._content = content
    def get(self): return {"name": "stdin", "content": self._content}


class FakeProcessContexts:
    def __init__(self, with_stdin: bool = True): self.with_stdin = with_stdin
    def get_contexts(self, session):
        ctx = []
        if self.with_stdin:
            ctx.append({"context": StdinContext("Say hi")})
        return ctx


class Provider:
    def __init__(self): self._raw = {"full": True}
    def chat(self): return "Completed text"
    def stream_chat(self): yield from []
    def get_full_response(self): return self._raw


class Session:
    def __init__(self, with_stdin=True):
        self.utils = FakeInputUtils()
        # Default non-stream, non-raw
        self._params = {"raw_completion": False}
        self._contexts = {}
        self._actions = {"process_contexts": FakeProcessContexts(with_stdin)}
        self._provider = Provider()
        self.config = type("C", (), {"overrides": {}})()

    def set_flag(self, k, v): pass
    def get_action(self, name): return self._actions.get(name)
    def add_context(self, name, value=None):
        if name == 'chat':
            ctx = self._contexts.get('chat') or ChatContext()
            self._contexts['chat'] = ctx
            return ctx
        self._contexts[name] = value
        return value
    def get_context(self, name): return self._contexts.get(name)
    def remove_context_type(self, name): self._contexts.pop(name, None)
    def get_params(self): return dict(self._params)
    def set_option(self, k, v): self._params[k] = v
    def get_provider(self): return self._provider


def test_completion_mode_non_stream_outputs_text():
    sess = Session(with_stdin=True)
    mode = CompletionMode(sess)
    mode.start()
    out = "".join(m + e for (m, e) in sess.utils.output.lines)
    assert "Completed text" in out


def test_completion_mode_raw_emits_raw_full_response():
    sess = Session(with_stdin=True)
    sess._params["raw_completion"] = True
    mode = CompletionMode(sess)
    mode.start()
    out = "".join(m + e for (m, e) in sess.utils.output.lines)
    assert "full" in out
