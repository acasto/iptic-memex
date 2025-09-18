from __future__ import annotations

import os
import sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modes.chat_mode import ChatMode


class FakeOutput:
    def __init__(self):
        self.lines = []

    def style_text(self, text, **kwargs):
        return text

    def write(self, message: str = "", end: str = "\n", flush: bool = False, **kwargs):
        self.lines.append((message, end))

    def suppress_stdout_blanks(self, *a, **k):
        class _Ctx:
            def __enter__(self2):
                return self
            def __exit__(self2, exc_type, exc, tb):
                return False
        return _Ctx()


class FakeInput:
    def __init__(self):
        self.calls = 0
    def get_input(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "hello"
        raise KeyboardInterrupt


class FakeUtils:
    def __init__(self):
        self.output = FakeOutput()
        self.input = FakeInput()
        self.tab_completion = type("TC", (), {"run": lambda *a, **k: None, "set_session": lambda *a, **k: None})()


class ChatContext:
    def __init__(self):
        self._msgs = []
    def add(self, content, role, contexts=None):
        self._msgs.append({"role": role, "content": content})
    def get(self, kind):
        if kind == "all":
            return list(self._msgs)
        return None


class Provider:
    def chat(self):
        return "assistant reply"
    def stream_chat(self):
        yield from []
    def get_usage(self):
        return {}
    def get_cost(self):
        return {"total_cost": 0}


class FakeProcessContexts:
    def process_contexts_for_user(self, auto_submit: bool):
        return []


class FakeChatCommands:
    def run(self, text):
        return False


class Session:
    def __init__(self):
        self.utils = FakeUtils()
        self._params = {
            "user_label": "You:",
            "user_label_color": None,
            "response_label": "Assistant:",
            "response_label_color": None,
            "stream": False,
        }
        self._flags = {}
        self._contexts = {}
        self._actions = {"process_contexts": FakeProcessContexts(), "chat_commands": FakeChatCommands()}

    def get_params(self): return dict(self._params)
    def set_option(self, k, v): self._params[k] = v
    def get_option(self, s, k, fallback=None): return fallback
    def get_flag(self, k): return self._flags.get(k)
    def set_flag(self, k, v): self._flags[k] = v
    def get_context(self, name): return self._contexts.get(name)
    def add_context(self, name, value=None):
        if name == 'chat':
            ctx = self._contexts.get('chat') or ChatContext()
            self._contexts['chat'] = ctx
            return ctx
        self._contexts[name] = value
        return value
    def remove_context_type(self, name): self._contexts.pop(name, None)
    def get_action(self, name): return self._actions.get(name)
    def get_provider(self): return Provider()
    def handle_exit(self, confirm=True): return True


def test_chat_mode_single_turn_non_stream_captures_output():
    sess = Session()
    mode = ChatMode(sess)
    # Run; it will do one turn, then raise KeyboardInterrupt which ChatMode re-raises
    with pytest.raises(KeyboardInterrupt):
        mode.start()

    # Verify the assistant label and reply were printed
    out = "".join(m + e for (m, e) in sess.utils.output.lines)
    assert "Assistant:" in out
    assert "assistant reply" in out
