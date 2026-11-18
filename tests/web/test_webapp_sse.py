import json

import pytest
from starlette.testclient import TestClient

from web.app import WebApp


@pytest.mark.parametrize('stream', [True, False])
def test_live_approval_resumes_original_turn(tool_session, stream):
    session, seen = tool_session(ask=True)
    with TestClient(WebApp(session)._app) as client:
        if stream:
            response = client.get('/api/stream', params={'message': 'go'})
            reply = json.loads(response.text.split('event: done\ndata: ')[1])
        else:
            reply = client.post('/api/chat', json={'message': 'go'}).json()
        assert seen == ['start']
        assert reply['needs_interaction']['kind'] == 'bool'
        token = reply['state_token']
        assert client.post('/api/chat', json={'message': 'again'}).status_code == 409
        assert client.post('/api/action/start', json={'action': 'clear_chat'}).status_code == 409
        assert client.post('/api/action/resume', json={'state_token': token, 'response': 'yes'}).status_code == 400
        final = client.post('/api/action/resume', json={'state_token': token, 'response': True})
        assert final.json()['text'] == 'Follow-up answer'
        assert seen == ['start', True]
        assert session.provider.calls == 2
        assert client.post('/api/action/resume', json={'state_token': token, 'response': True}).status_code == 400
        history = session.get_context('chat').get('all')
        assert [t['role'] for t in history] == ['user', 'assistant', 'tool', 'assistant']
        assert history[2]['tool_call_id'] == 'probe-1'


def test_cancel_pending_input_releases_session(tool_session):
    session, seen = tool_session(ask=True)
    with TestClient(WebApp(session)._app) as client:
        reply = client.post('/api/chat', json={'message': 'go'}).json()
        stopped = client.post('/api/action/cancel', json={'state_token': reply['state_token']})
        assert stopped.json()['status'] == 'cancelled'
        assert session.provider.calls == 1 and seen == ['start']
        assert client.post('/api/chat', json={'message': 'next'}).json()['ok']
