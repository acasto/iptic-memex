from __future__ import annotations

"""
Lightweight self-test for WebApp endpoints using Starlette TestClient.

Runs without pytest and uses a FakeSession with:
 - Provider that returns fixed text for chat and chunked stream
 - User commands to trigger a stepwise action
 - Stepwise action that requests text input then completes

Usage:
  python web/selftest.py
"""

from configparser import ConfigParser
from typing import Any, Dict, List, Optional

try:
    from starlette.testclient import TestClient
except Exception as e:  # pragma: no cover
    print("[SKIP] starlette not available:", e)
    raise

import os, sys
# Ensure project root is on sys.path when executed as a script
_here = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_here, os.pardir))
if _root not in sys.path:
    sys.path.insert(0, _root)

from web.app import WebApp
from ui.web import WebUI
from base_classes import InteractionNeeded, Completed
from component_registry import ComponentRegistry
from config_manager import SessionConfig
from session import Session


class FakeProvider:
    def __init__(self) -> None:
        self._usage = {"input": 1, "output": 1}
        self._cost = 0.0001

    def chat(self) -> str:
        return "Hello from provider!"

    def stream_chat(self):
        for ch in ["Hello", " ", "from", " ", "provider!"]:
            yield ch

    def get_usage(self) -> Dict[str, Any]:
        return self._usage

    def get_cost(self) -> float:
        return self._cost


class FakeStepwiseAction:
    name = "fake_step"

    def __init__(self, session) -> None:
        self.session = session

    # Provide a run shim so /api/chat command path can trigger InteractionNeeded
    def run(self, argv: List[str] | None = None):
        spec = {"prompt": "Enter a value:", "default": "ok", "__action__": self.name, "__args__": {"argv": argv or []}, "__content__": None}
        raise InteractionNeeded("text", spec, "UNISSUED")

    def start(self, args: Dict[str, Any], content: Optional[str]):
        # Ask for a line of text
        spec = {"prompt": "Enter a value:", "default": "ok", "__action__": self.name, "__args__": args, "__content__": content}
        raise InteractionNeeded("text", spec, "UNISSUED")

    def resume(self, state_token: str, data: Dict[str, Any]):
        resp = None
        if isinstance(data, dict):
            resp = data.get("response")
        # Emit an update
        try:
            self.session.ui.emit("status", {"message": f"Received: {resp}"})
        except Exception:
            pass
        return Completed({"echo": resp})


class FakeSession(Session):
    """A real interactive session with isolated configuration and an offline provider."""

    def __init__(self):
        settings, models = ConfigParser(), ConfigParser()
        settings.read_dict({'DEFAULT': {'default_model': 'fake-model'},
                                      'TOOLS': {'tool_mode': 'official'}})
        models.read_dict({'fake-model': {'provider': 'fake'}})
        config = SessionConfig(settings, models)
        super().__init__(config, ComponentRegistry(config), private=True)
        self.ui = WebUI(self)
        self.provider = FakeProvider()
        self._actions = {'fake_step': FakeStepwiseAction(self)}
        self.add_context('chat')

    def get_action(self, name):
        return self._actions.get(name) or super().get_action(name)


def run_selftest() -> int:
    sess = FakeSession()
    app = WebApp(sess)
    client = TestClient(app._app)

    failures = 0
    def check(cond: bool, label: str) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), '-', label)
        if not cond:
            failures += 1

    def to_json(resp):
        try:
            return resp.json()
        except Exception:
            try:
                print("[DEBUG] Non-JSON response status=", resp.status_code)
                print("[DEBUG] Body=", resp.text)
            except Exception:
                pass
            return None

    # 1) Status
    r = client.get("/api/status")
    ok = r.status_code == 200 and r.json().get("ok") is True
    check(ok, "/api/status returns ok")

    # 2) Non-stream chat
    r = client.post("/api/chat", json={"message": "hello"})
    j = r.json()
    check(r.status_code == 200 and j.get("ok") is True and "Hello" in (j.get("text") or ""), "/api/chat returns provider text")

    # 3) Stream chat (SSE): collect body and ensure done event appears
    with client.stream("GET", "/api/stream?message=hello") as s:
        chunks = []
        for c in s.iter_text():
            chunks.append(c)
        body = "".join(chunks)
        check("event: done" in body and "Hello" in body, "/api/stream returns done event with text")

    # 4) Direct action start triggers interaction
    r = client.post("/api/action/start", json={"action": "fake_step", "args": {"argv": []}, "content": None})
    j = to_json(r) or {}
    token = j.get("state_token")
    ok_start = j.get("ok") is True and j.get("done") is False and token
    if not ok_start:
        try:
            print("[DEBUG] start status=", r.status_code)
            print("[DEBUG] start json=", j)
            print("[DEBUG] start body=", r.text)
        except Exception:
            pass
    check(ok_start, "Action start yields needs_interaction with token")

    # 5) Resume interaction
    if token:
        r2 = client.post("/api/action/resume", json={"state_token": token, "response": "world"})
        j2 = to_json(r2) or {}
        check(j2.get("ok") is True and j2.get("done") is True and j2.get("payload", {}).get("echo") == "world", "Resume completes with payload")
    else:
        check(False, "Resume skipped due to missing token")

    # 6) Token reuse should fail
    if token:
        r3 = client.post("/api/action/resume", json={"state_token": token, "response": "again"})
        j3 = to_json(r3) or {}
        check(j3.get("ok") is False and "already used" in (j3.get("error", {}).get("message", "").lower()), "Token reuse rejected")
    else:
        check(False, "Reuse skipped due to missing token")

    # 7) Expired token should be rejected (manually age a token)
    # Start a new interaction to get a token
    r4 = client.post("/api/action/start", json={"action": "fake_step", "args": {"argv": []}})
    j4 = to_json(r4) or {}
    token2 = j4.get("state_token")
    if not token2:
        check(False, "Expiry setup yields a fresh interaction token")
        return failures
    # Age state
    st = app._states.get(token2)
    if st:
        st.issued_at -= 999999
    r5 = client.post("/api/action/resume", json={"state_token": token2, "response": "later"})
    j5 = to_json(r5) or {}
    check(j5.get("ok") is False and "expired" in (j5.get("error", {}).get("message", "").lower()), "Expired token rejected")

    return failures


if __name__ == "__main__":
    try:
        failures = run_selftest()
        if failures:
            raise SystemExit(1)
    except Exception as e:
        print("Selftest error:", e)
        raise
