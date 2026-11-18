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



def test_uploaded_batch_belongs_to_its_submitted_question():
    from pathlib import Path
    from starlette.testclient import TestClient
    from web.app_factory import create_app
    from web.selftest import FakeSession

    session = FakeSession()
    with TestClient(create_app(session)) as client:
        upload = client.post('/api/upload', files={'files': ('note.txt', b'attached evidence')}).json()
        token = upload['upload_token']
        assert client.post('/api/chat', json={'message': 'unrelated'}).json()['ok']
        answer = client.post('/api/chat', json={'message': 'use note', 'upload_token': token})
        assert answer.json()['ok'], answer.text
        turns = [t for t in session.get_context('chat').get('all') if t['role'] == 'user']
        assert not turns[0].get('context')
        assert turns[1]['context'][0]['context'].get()['content'] == 'attached evidence'
        assert not Path(upload['files'][0]['path']).exists()
        assert client.post('/api/chat', json={'message': 'reuse', 'upload_token': token}).status_code == 400


def test_commands_and_provider_failures_are_distinct_from_answers():
    from starlette.testclient import TestClient
    from web.app_factory import create_app
    from web.selftest import FakeSession

    session = FakeSession()
    session.set_option('api_key', 'test-only')
    session.set_option('temperature', 0.1)
    with TestClient(create_app(session)) as client:
        assert 'api_key' not in client.get('/api/params').json()['params']
        assert client.post('/api/chat', json={'message': '/unknown_command'}).status_code == 400
        changed = client.post('/api/chat', json={'message': '/set option temperature 0.42'})
        assert changed.json()['handled'] and changed.json()['done']
        assert float(session.get_params()['temperature']) == 0.42
        assert session.get_context('chat').get('all') == []

        def fail():
            raise RuntimeError('Offline failure')
        session.provider.chat = fail
        response = client.post('/api/chat', json={'message': 'hello'})
        assert response.status_code == 502 and response.json()['status'] == 'failed'
