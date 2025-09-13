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


def test_static_assets_and_index():
    _require_starlette()
    from starlette.testclient import TestClient
    from web.app import WebApp
    from web.selftest import FakeSession

    sess = FakeSession()
    app = WebApp(sess)
    client = TestClient(app._app)

    # Index should load and reference main.js as ES module
    r = client.get('/')
    assert r.status_code == 200
    body = r.text
    # Allow optional cache-busting query string (e.g., ?v=3)
    assert '<script type="module" src="/static/js/main.js' in body

    # New JS modules should be served by StaticFiles
    for path in [
        '/static/js/main.js',
        '/static/js/sse.js',
        '/static/js/api.js',
        '/static/js/bus.js',
        '/static/js/store.js',
        '/static/js/raf_batch.js',
        '/static/js/controller.js',
    ]:
        rr = client.get(path)
        assert rr.status_code == 200, f"{path} not served"
        assert len(rr.text) > 20

    # Index links stylesheet and includes jump-to-latest control
    # Allow optional version query on CSS as well
    assert '<link rel="stylesheet" href="/static/css/main.css' in body
    assert 'id="jumpLatest"' in body
    assert 'id="newchat"' in body

    # CSS is served and contains scroll styling for #log
    css = client.get('/static/css/main.css')
    assert css.status_code == 200
    assert '#log' in css.text and ('overflow-y: auto' in css.text or 'overflow-y:auto' in css.text)

    # Main.js exposes an Updates header (implementation evolved to use panel-title)
    mj = client.get('/static/js/main.js').text
    assert ("title.textContent = 'Updates'" in mj) or ('panel-title">Updates<' in mj)
