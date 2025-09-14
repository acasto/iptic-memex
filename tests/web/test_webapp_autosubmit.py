from __future__ import annotations

import os
import sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _require_starlette():
    try:
        from starlette.testclient import TestClient  # noqa: F401
    except Exception as e:
        pytest.skip(f"starlette not available: {e}")


class ChatContext:
    def __init__(self):
        self._msgs = []

    def add(self, content: str, role: str, contexts: list | None = None):
        self._msgs.append({"role": role, "content": content, "contexts": contexts or []})

    def get(self, kind: str):
        if kind == "all":
            return list(self._msgs)
        return None


class ProviderFollowUp:
    def __init__(self, text: str = "Follow-up from provider"):
        self._text = text

    def chat(self) -> str:
        return self._text

    def stream_chat(self):
        yield from []

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


class FakeProcessContexts:
    def process_contexts_for_user(self, auto_submit: bool):
        return []


class AutoSubmitAction:
    name = "auto_submit_action"

    def __init__(self, session):
        self.session = session

    def run(self, args=None, content=None):
        from base_classes import Completed
        self.session.set_flag("auto_submit", True)
        return Completed({"status": "ok"})

    def start(self, args, content):
        from base_classes import Completed
        # Signal that after completing, server should run an assistant turn
        self.session.set_flag("auto_submit", True)
        return Completed({"status": "ok"})


class SessionAuto:
    def __init__(self):
        from ui.web import WebUI
        self._flags = {}
        self._params = {"stream": False, "model": "fake", "provider": "fake"}
        self._contexts = {"chat": ChatContext()}
        self._actions = {"process_contexts": FakeProcessContexts(), "auto_submit_action": AutoSubmitAction(self)}
        self._provider = ProviderFollowUp()
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


def test_web_action_done_triggers_auto_submit_turn():
    _require_starlette()
    from starlette.testclient import TestClient
    from web.app import WebApp

    sess = SessionAuto()
    app = WebApp(sess)
    client = TestClient(app._app)

    r = client.post("/api/action/start", json={"action": "auto_submit_action", "args": {}, "content": None})
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True and j.get("done") is True
    # The server should have run a follow-up assistant turn and included visible text
    assert "Follow-up" in (j.get("text") or "")
