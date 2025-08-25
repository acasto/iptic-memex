from __future__ import annotations

import os
import sys
import json
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _require_starlette():
    try:
        from starlette.testclient import TestClient  # noqa: F401
    except Exception as e:
        pytest.skip(f"starlette not available: {e}")


class ProviderABC:
    def __init__(self):
        self._text = "abc"

    def chat(self) -> str:
        return self._text

    def stream_chat(self):
        for ch in self._text:
            yield ch

    def get_usage(self):
        return {}

    def get_cost(self):
        return 0.0


class SessionABC:
    def __init__(self):
        from ui.web import WebUI
        self._params = {"stream": True}
        self._flags = {}
        self._actions = {}
        self._contexts = {"chat": type("C", (), {"add": lambda *a, **k: None, "get": lambda *a, **k: None})()}
        self._provider = ProviderABC()
        self.utils = type("U", (), {"output": type("O", (), {"write": lambda *a, **k: None})(), "replace_output": lambda self2, out: None})()
        self.ui = WebUI(self)

    def get_params(self):
        return dict(self._params)

    def set_option(self, key, value):
        self._params[key] = value

    def get_option(self, section, key, fallback=None):
        return fallback

    def get_flag(self, name):
        return self._flags.get(name)

    def set_flag(self, name, value):
        self._flags[name] = value

    def get_context(self, name):
        return self._contexts.get(name)

    def add_context(self, name, value=None):
        self._contexts[name] = value
        return value

    def remove_context_type(self, name):
        self._contexts.pop(name, None)

    def get_action(self, name):
        return self._actions.get(name)

    def get_provider(self):
        return self._provider


def test_stream_emits_tokens_and_done_text():
    _require_starlette()
    from starlette.testclient import TestClient
    from web.app_factory import create_app

    sess = SessionABC()
    app = create_app(sess)
    client = TestClient(app)

    # Use backward-compatible message param to avoid token bootstrap
    with client.stream("GET", "/api/stream?message=go") as s:
        body = "".join(s.iter_text())

    # There should be token events for 'a', 'b', 'c' and a final done with text 'abc'
    assert "event: token" in body
    assert body.count("event: token") >= 1
    assert "event: done" in body
    # Extract last done payload
    blocks = [b for b in body.split("\n\n") if b.strip()]
    done_lines = [b for b in blocks if b.startswith("event: done")]
    assert done_lines
    payload = done_lines[-1].split("data:", 1)[1].strip()
    j = json.loads(payload)
    assert j.get("text") == "abc"

