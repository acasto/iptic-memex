from __future__ import annotations

import os
import sys
import json
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


def test_stream_emits_tokens_and_done_text():
    _require_starlette()
    from starlette.testclient import TestClient
    from web.app_factory import create_app

    sess = FakeSession()
    sess.provider = ProviderABC()
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

