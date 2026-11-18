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


def test_action_start_resume_via_factory():
    _require_starlette()
    from starlette.testclient import TestClient
    from web.app_factory import create_app
    from web.selftest import FakeSession

    sess = FakeSession()
    app = create_app(sess)
    client = TestClient(app)

    # Start an action that asks for input
    r = client.post("/api/action/start", json={"action": "fake_step", "args": {}, "content": None})
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True and j.get("done") is False
    token = j.get("state_token")
    assert isinstance(token, str) and token

    # Resume with a response
    r2 = client.post("/api/action/resume", json={"state_token": token, "response": "xyz"})
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2.get("ok") is True and j2.get("done") is True
    assert j2.get("payload", {}).get("echo") == "xyz"

    # Reuse should fail
    r3 = client.post("/api/action/resume", json={"state_token": token, "response": "again"})
    j3 = r3.json()
    assert j3.get("ok") is False
    assert "already" in (j3.get("error", {}).get("message", "").lower())



def test_two_step_option_command_retains_its_selected_option():
    from starlette.testclient import TestClient
    from web.app_factory import create_app
    from web.selftest import FakeSession

    session = FakeSession()
    session.set_option('temperature', 0.1)
    with TestClient(create_app(session)) as client:
        ask_name = client.post('/api/chat', json={'message': '/set option'}).json()
        ask_value = client.post('/api/action/resume', json={
            'state_token': ask_name['state_token'], 'response': 'temperature',
        }).json()
        assert ask_value['needs_interaction']['kind'] == 'text'
        result = client.post('/api/action/resume', json={
            'state_token': ask_value['state_token'], 'response': '0.6',
        }).json()
        assert result['payload']['ok'] and session.get_params()['temperature'] == 0.6
