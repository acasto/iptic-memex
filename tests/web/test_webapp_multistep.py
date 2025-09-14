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


class MultiStepAction:
    name = "multi_step"

    def __init__(self, session):
        self.session = session
        self._phase = 0

    def start(self, args, content):
        # Emit an update; then request input
        from base_classes import Updates, InteractionNeeded
        self._phase = 1
        upd = Updates([{"type": "status", "message": "starting"}])
        # Web server will resume("__implicit__") and catch InteractionNeeded next
        try:
            return upd
        finally:
            # after emitting updates, next resume call should ask for input
            pass

    def resume(self, state_token, data):
        from base_classes import Updates, Completed, InteractionNeeded
        # implicit continue => ask for input
        if data == {"continue": True} or (isinstance(data, dict) and data.get("continue")):
            spec = {"prompt": "enter value", "default": "x", "__action__": self.name, "__args__": {}, "__content__": None}
            raise InteractionNeeded("text", spec, "UNISSUED")
        # user response
        resp = data.get("response") if isinstance(data, dict) else None
        # emit another update then complete
        ev = Updates([{"type": "status", "message": f"got:{resp}"}])
        # Web will drive until boundary and then return Completed
        return Completed({"value": resp}) if False else ev

    # Add a run entrypoint so routes can call run() uniformly
    def run(self, args=None, content=None):
        return self.start(args, content)


def _mk_client_with_action():
    from starlette.testclient import TestClient
    from web.app import WebApp
    from web.selftest import FakeSession
    sess = FakeSession()
    # Register multi-step action
    sess._actions["multi_step"] = MultiStepAction(sess)  # type: ignore[attr-defined]
    app = WebApp(sess)
    return TestClient(app._app), app


def test_web_action_multistep_flow():
    _require_starlette()
    client, _ = _mk_client_with_action()
    # Start -> expect needs_interaction with token and an update
    r1 = client.post("/api/action/start", json={"action": "multi_step", "args": {}, "content": None})
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1.get("ok") is True and j1.get("done") is False
    assert j1.get("state_token")
    assert j1.get("updates") and j1["updates"][0].get("message") == "starting"
    tok = j1["state_token"]

    # Resume -> should complete after emitting an update
    r2 = client.post("/api/action/resume", json={"state_token": tok, "response": "abc"})
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2.get("ok") is True
    # Because action returns Updates first, server will drive, collect updates, then complete
    # Payload may be attached only on 'done' paths; ensure we got updates containing the user response
    assert j2.get("updates") and any(u.get("message") == "got:abc" for u in j2["updates"])
