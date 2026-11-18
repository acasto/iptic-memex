from __future__ import annotations

import os
import sys
import pytest

from web.selftest import FakeSession

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _require_starlette():
    try:
        from starlette.testclient import TestClient  # noqa: F401
    except Exception as e:
        pytest.skip(f"starlette not available: {e}")


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


def test_web_action_done_triggers_auto_submit_turn():
    _require_starlette()
    from starlette.testclient import TestClient
    from web.app import WebApp

    sess = FakeSession()
    sess.provider = ProviderFollowUp()
    sess._actions['auto_submit_action'] = AutoSubmitAction(sess)
    app = WebApp(sess)
    client = TestClient(app._app)

    r = client.post("/api/action/start", json={"action": "auto_submit_action", "args": {}, "content": None})
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True and j.get("done") is True
    # The server should have run a follow-up assistant turn and included visible text
    assert "Follow-up" in (j.get("text") or "")
