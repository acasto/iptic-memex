from __future__ import annotations

import os
import sys
import re

import pytest


# Ensure project root is importable when running via pytest
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _require_starlette():
    try:
        from starlette.testclient import TestClient  # noqa: F401
    except Exception as e:
        pytest.skip(f"starlette not available: {e}")


def _mk_client():
    from starlette.testclient import TestClient
    from web.app import WebApp
    from web.selftest import FakeSession
    sess = FakeSession()
    app = WebApp(sess)
    return TestClient(app._app), app


def test_status_ok():
    _require_starlette()
    client, _ = _mk_client()
    r = client.get("/api/status")
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True


def test_chat_non_stream_returns_text():
    _require_starlette()
    client, _ = _mk_client()
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert "Hello" in (j.get("text") or "")


def test_stream_sse_done_event_contains_text():
    _require_starlette()
    client, _ = _mk_client()
    with client.stream("GET", "/api/stream?message=hello") as s:
        data = "".join(s.iter_text())
    assert "event: done" in data
    assert "Hello" in data


def test_action_interaction_start_and_resume():
    _require_starlette()
    client, app = _mk_client()

    # Start: should need interaction with token
    r = client.post("/api/action/start", json={"action": "fake_step", "args": {"argv": []}, "content": None})
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert j.get("done") is False
    token = j.get("state_token")
    assert isinstance(token, str) and token

    # Resume: should complete with payload
    r2 = client.post("/api/action/resume", json={"state_token": token, "response": "world"})
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2.get("ok") is True and j2.get("done") is True
    assert j2.get("payload", {}).get("echo") == "world"

    # Reuse: should be rejected
    r3 = client.post("/api/action/resume", json={"state_token": token, "response": "again"})
    assert r3.status_code in (400, 200)
    j3 = r3.json()
    assert j3.get("ok") is False
    assert "already" in (j3.get("error", {}).get("message", "").lower())

    # Expired: start another, then force expire and resume
    r4 = client.post("/api/action/start", json={"action": "fake_step", "args": {"argv": []}, "content": None})
    j4 = r4.json()
    token2 = j4.get("state_token")
    assert token2
    st = app._states.get(token2)
    assert st is not None
    st.issued_at -= 999999
    r5 = client.post("/api/action/resume", json={"state_token": token2, "response": "later"})
    j5 = r5.json()
    assert j5.get("ok") is False
    assert "expired" in (j5.get("error", {}).get("message", "").lower())

