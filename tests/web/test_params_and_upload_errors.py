from __future__ import annotations

import os
import sys
import json
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _mk_client():
    from starlette.testclient import TestClient
    from web.app import WebApp
    from web.selftest import FakeSession
    sess = FakeSession()
    app = WebApp(sess)
    return TestClient(app._app)


def test_api_params_returns_current_params():
    try:
        from starlette.testclient import TestClient  # ensure starlette
    except Exception as e:
        pytest.skip(f"starlette not available: {e}")
    client = _mk_client()
    r = client.get('/api/params')
    assert r.status_code == 200
    j = r.json()
    assert j.get('ok') is True
    assert isinstance(j.get('params'), dict)


def test_api_upload_with_non_multipart_returns_json_error():
    try:
        from starlette.testclient import TestClient  # ensure starlette
    except Exception as e:
        pytest.skip(f"starlette not available: {e}")
    client = _mk_client()
    r = client.post('/api/upload', json={'foo': 'bar'})
    # Should be a 400 with JSON error envelope
    assert r.status_code == 400
    j = r.json()
    assert j.get('ok') is False
    assert 'error' in j
