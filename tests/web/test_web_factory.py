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


def test_create_app_factory_basic():
    _require_starlette()
    from starlette.testclient import TestClient
    from web.app_factory import create_app
    from web.selftest import FakeSession

    sess = FakeSession()
    app = create_app(sess)
    client = TestClient(app)

    r = client.get("/api/status")
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True

    r2 = client.post("/api/chat", json={"message": "hello"})
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2.get("ok") is True
    assert "Hello" in (j2.get("text") or "")

