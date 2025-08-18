from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from modes.agent_mode import AgentMode


class FakeOutput:
    def __init__(self): self.lines = []
    def write(self, message: str = "", end: str = "\n", **kwargs): self.lines.append((message, end))
    def suppress_stdout_blanks(self, *a, **k):
        class _Ctx:
            def __enter__(self2): return self
            def __exit__(self2, exc_type, exc, tb): return False
        return _Ctx()
    def error(self, *a, **k): pass


class ChatContext:
    def __init__(self): self._msgs = []
    def add(self, content, role, contexts=None): self._msgs.append({"role": role, "content": content})
    def get(self, kind): return list(self._msgs) if kind == 'all' else None


class Provider:
    def __init__(self): self._full = {"raw": True}
    def chat(self): return "Result here %%DONE%%"
    def stream_chat(self):
        yield from []
    def get_full_response(self): return self._full
    def get_usage(self): return {}
    def reset_usage(self): pass
    def get_cost(self): return {"total_cost": 0}


class Session:
    def __init__(self):
        self.utils = type("U", (), {"output": FakeOutput()})()
        self._params = {"agent_output_mode": "final", "raw_completion": False}
        self._contexts = {}
        self._provider = Provider()
        self._options = {}
        self.config = type("C", (), {"overrides": {}})()
    def get_params(self): return dict(self._params)
    def set_option(self, k, v): self._params[k] = v
    def get_option(self, s, k, fallback=None): return fallback
    def get_context(self, name): return self._contexts.get(name)
    def add_context(self, name, value=None):
        if name == 'chat':
            ctx = self._contexts.get('chat') or ChatContext()
            self._contexts['chat'] = ctx
            return ctx
        self._contexts[name] = value
        return value
    def get_provider(self): return self._provider
    def enter_agent_mode(self, policy): self._policy = policy
    def exit_agent_mode(self): pass
    def get_agent_write_policy(self): return 'deny'


def test_agent_mode_final_outputs_trimmed_text_and_stops_on_done():
    sess = Session()
    mode = AgentMode(sess, steps=3)
    mode.start()
    out = "".join(m + e for (m, e) in sess.utils.output.lines)
    assert "Result here" in out
    assert "%%DONE%%" not in out

