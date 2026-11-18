import json

import pytest
from starlette.testclient import TestClient

from web.app_factory import create_app


@pytest.mark.parametrize('pseudo', [True, False])
def test_stream_executes_one_tool_once(tool_session, pseudo):
    session, seen = tool_session(pseudo=pseudo)
    with TestClient(create_app(session)) as client:
        token = client.post('/api/stream/start', json={'message': 'go'}).json()['token']
        response = client.get('/api/stream', params={'token': token})
        packet = response.text.split('event: done\ndata: ')[1]
        done = json.loads(packet)
        assert seen == ['start']
        assert session.provider.calls == 2
        assert done['text'] == 'Follow-up answer'
        assert sum(u['message'] == 'tool ran' for u in done['updates']) == 1
        assert client.get('/api/stream', params={'token': token}).status_code == 400
