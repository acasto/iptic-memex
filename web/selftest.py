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

import json
import time
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
from base_classes import InteractionNeeded, Completed, Updates


class FakeOutput:
    def __init__(self) -> None:
        self._buf: List[str] = []

    def write(self, message: Any = "", **kwargs: Any) -> None:
        self._buf.append(str(message))

    def pop(self) -> str:
        s = "".join(self._buf)
        self._buf.clear()
        return s


class FakeUtils:
    def __init__(self) -> None:
        self.output = FakeOutput()

    def replace_output(self, out) -> None:
        self.output = out


class ChatContext:
    def __init__(self) -> None:
        self._msgs: List[Dict[str, Any]] = []

    def add(self, content: str, role: str, contexts: Optional[list] = None) -> None:
        self._msgs.append({"role": role, "content": content, "contexts": contexts or []})

    def get(self, kind: str) -> Any:
        if kind == "all":
            return list(self._msgs)
        return None


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


class FakeProcessContexts:
    def get_contexts(self, session) -> list:
        return []


class FakeAssistantCommands:
    def parse_commands(self, text: str) -> list:
        return []


class FakeUserCommands:
    def __init__(self) -> None:
        # Map a simple command to the fake stepwise action
        self.commands = {
            "fake:ask": {
                "description": "Trigger fake stepwise ask",
                "function": {"type": "action", "name": "fake_step"},
            }
        }


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


class FakeSession:
    def __init__(self) -> None:
        self.utils = FakeUtils()
        self._contexts: Dict[str, Any] = {"chat": ChatContext()}
        self._actions: Dict[str, Any] = {}
        self._flags: Dict[str, Any] = {}
        self._params: Dict[str, Any] = {"stream": False, "model": "fake-model", "provider": "fake"}
        # UI is WebUI; the server will intercept emit for capture
        self.ui = WebUI(self)

        # Install known actions
        self._actions["process_contexts"] = FakeProcessContexts()
        self._actions["assistant_commands"] = FakeAssistantCommands()
        self._actions["user_commands"] = FakeUserCommands()
        self._actions["fake_step"] = FakeStepwiseAction(self)

    def get_params(self) -> Dict[str, Any]:
        return dict(self._params)

    def set_option(self, key: str, value: Any) -> None:
        self._params[key] = value

    def get_option(self, section: str, key: str, fallback: Any = None) -> Any:
        # Only TOOLS.allow_auto_submit in runner path; return fallback
        return fallback

    def get_flag(self, name: str) -> Any:
        return self._flags.get(name)

    def set_flag(self, name: str, value: Any) -> None:
        self._flags[name] = value

    def get_context(self, name: str):
        return self._contexts.get(name)

    def add_context(self, name: str, value: Optional[Any] = None):
        if name == "chat":
            ctx = self._contexts.get("chat") or ChatContext()
            self._contexts["chat"] = ctx
            return ctx
        self._contexts[name] = value
        return value

    def remove_context_type(self, name: str) -> None:
        if name in self._contexts and name not in ("prompt", "chat"):
            self._contexts.pop(name, None)

    def get_action(self, name: str):
        return self._actions.get(name)

    def get_provider(self):
        return FakeProvider()


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
    r4 = client.post("/api/chat", json={"message": "fake:ask"})
    j4 = r4.json()
    token2 = j4.get("state_token")
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
