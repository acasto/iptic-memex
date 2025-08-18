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


class ProviderStream:
    def __init__(self, text: str):
        self._text = text

    def chat(self) -> str:
        return self._text

    def stream_chat(self):
        for ch in self._text:
            yield ch

    def get_messages(self):
        return []

    def get_full_response(self):
        return None

    def get_usage(self):
        return {}

    def reset_usage(self):
        pass

    def get_cost(self):
        return 0.0


class ChatContext:
    def __init__(self):
        self._msgs = []

    def add(self, content: str, role: str, contexts: list | None = None):
        self._msgs.append({"role": role, "content": content, "contexts": contexts or []})

    def get(self, kind: str):
        if kind == "all":
            return list(self._msgs)
        return None


class CommandsEmit:
    def __init__(self, session):
        self.session = session

    def parse_commands(self, text: str):
        return ["tool"] if "run tool" in text else []

    def run(self, text: str):
        # Emit a status update
        self.session.ui.emit("status", {"message": "tool ran"})


class CommandsNeed:
    def __init__(self, session):
        self.session = session

    def parse_commands(self, text: str):
        return ["tool"] if "need input" in text else []

    def run(self, text: str):
        from base_classes import InteractionNeeded
        spec = {"prompt": "enter", "__action__": "fake", "__args__": {}, "__content__": None}
        raise InteractionNeeded("text", spec, "UNISSUED")


class SessionBase:
    def __init__(self, provider, commands_action):
        from ui.web import WebUI
        self._params = {"stream": True, "model": "fake", "provider": "fake"}
        self._flags = {}
        self._actions = {"assistant_commands": commands_action(self)}
        self._contexts = {"chat": ChatContext()}
        self._provider = provider
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
        if name == "chat":
            ctx = self._contexts.get("chat") or ChatContext()
            self._contexts["chat"] = ctx
            return ctx
        self._contexts[name] = value
        return value

    def remove_context_type(self, name):
        self._contexts.pop(name, None)

    def get_action(self, name):
        return self._actions.get(name)

    def get_provider(self):
        return self._provider


def _open_stream(client, msg: str):
    with client.stream("GET", f"/api/stream?message={msg}") as s:
        data = "".join(s.iter_text())
    # Extract last done event JSON payload
    blocks = [b for b in data.split("\n\n") if b.strip()]
    done_lines = [b for b in blocks if b.startswith("event: done")]
    assert done_lines, f"no done event in stream: {data}"
    last = done_lines[-1]
    # next line after event is 'data: {json}'
    payload = last.split("data:", 1)[1].strip()
    return json.loads(payload)


def test_stream_done_includes_updates_from_emits():
    _require_starlette()
    from starlette.testclient import TestClient
    from web.app import WebApp

    sess = SessionBase(ProviderStream("please run tool now"), CommandsEmit)
    app = WebApp(sess)
    client = TestClient(app._app)

    done = _open_stream(client, "hello")
    assert done.get("text", "") is not None
    updates = done.get("updates") or []
    assert any(u.get("message") == "tool ran" for u in updates)


def test_stream_handoff_produces_needs_interaction():
    _require_starlette()
    from starlette.testclient import TestClient
    from web.app import WebApp

    sess = SessionBase(ProviderStream("we will need input"), CommandsNeed)
    app = WebApp(sess)
    client = TestClient(app._app)

    done = _open_stream(client, "hello")
    assert done.get("handled") is True
    assert done.get("needs_interaction")
    assert done.get("state_token")

