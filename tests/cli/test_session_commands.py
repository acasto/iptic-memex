"""Exercise the persistent session CLI and its handoff to interactive clients."""
from configparser import ConfigParser
import json

from click.testing import CliRunner
import pytest

import main
from component_registry import ComponentRegistry
from config_manager import ConfigManager
from core.session_persistence import latest_session_path, session_lock


@pytest.fixture
def client(tmp_path, monkeypatch):
    manager = ConfigManager.__new__(ConfigManager)
    manager.base_config = ConfigParser()
    manager.base_config.read_dict({
        'DEFAULT': {'default_model': 'chat', 'template_handler': 'none', 'stream': 'false',
                    'user_label': 'You', 'user_label_color': 'blue',
                    'response_label': 'Assistant', 'response_label_color': 'green'},
        'Fake': {'active': 'true'},
        'AGENT': {'default_model': 'other'},
        'SESSIONS': {'session_directory': str(tmp_path), 'session_autosave': 'false',
                     'programmatic_session_autosave_limit': '2', 'session_autosave_limit': '1'},
    })
    manager.models = ConfigParser()
    manager.models.read_dict({name: {'provider': 'Fake'} for name in ('chat', 'other')})
    monkeypatch.setattr(main, 'ConfigManager', lambda _conf: manager)
    calls = []

    class Provider:
        def __init__(self, session):
            self.session = session
            calls.append(session)

        def chat(self):
            history = self.session.get_context('chat').get('all')
            if history[-1]['message'] == 'fail':
                raise RuntimeError('offline provider failure')
            inputs = [turn['message'] for turn in history if turn['role'] == 'user']
            return self.session.get_params()['model'] + ' saw ' + ' / '.join(inputs)

    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda *args: Provider)

    def invoke(*args, stdin=None, code=0):
        result = CliRunner().invoke(main.cli, args, input=stdin)
        assert result.exit_code == code, (result.output, result.exception)
        return result.stdout.strip()

    return invoke, manager, calls


def test_cross_invocation_chat_and_discovery(client, tmp_path):
    run, manager, calls = client
    sid = run('session', 'new', '--model', 'other', '--title', 'Consult')
    assert run('session', 'read', sid, '--json') == '[]'
    assert json.loads(run('session', 'list', '--json')) == []
    assert json.loads(run('session', 'list', '--all', '--json'))[0]['origin'] == 'programmatic'
    assert not calls
    assert run('session', 'send', sid, 'Juniper') == 'other saw Juniper'
    reply = json.loads(run('session', 'send', sid, '--stdin-message', '--json', stdin='Recall'))
    assert reply['status'] == 'completed' and reply['last_text'] == 'other saw Juniper / Recall'
    assert len(calls) == 2 and all(s._closed for s in calls)
    path = tmp_path / f'{sid}.ims.json'
    before = path.read_bytes()
    assert json.loads(run('session', 'search', sid, 'JUNIPER', '--last', '1', '--json')) == [
        {'index': 4, 'role': 'assistant', 'text': 'other saw Juniper / Recall'}]
    assert '[3] user: Recall' in run('session', 'read', sid, '--last', '2')
    assert path.read_bytes() == before and len(calls) == 2
    assert latest_session_path(manager.create_session_config()) == ''
    output = run('chat', '--resume', sid, stdin='again\n/save session\n/quit\n')
    assert 'other saw Juniper / Recall / again' in output
    assert json.loads(path.read_text())['origin'] == 'programmatic'


def test_interactive_handoff_and_retention(client, tmp_path):
    run, manager, calls = client
    run('chat', stdin='My question\n/save session Handoff\n/quit\n')
    item, = json.loads(run('session', 'list', '--json'))
    sid = item['id']
    path = tmp_path / f'{sid}.ims.json'
    # Simulate a conversation saved before origin metadata existed.
    legacy = json.loads(path.read_text())
    del legacy['origin']
    path.write_text(json.dumps(legacy))
    assert 'chat saw My question / Follow-up' == run('session', 'send', sid, 'Follow-up')
    assert json.loads(path.read_text())['origin'] == 'interactive'
    first, second, third = [run('session', 'new') for _ in range(3)]
    items = json.loads(run('session', 'list', '--origin', 'programmatic', '--json'))
    assert [row['id'] for row in items] == [third, second]
    assert {r['id'] for r in json.loads(run('session', 'list', '--all', '--json'))} == {
        sid, second, third}
    assert latest_session_path(manager.create_session_config()) == str(path)
    assert first not in run('list-sessions', '--all')
    assert third not in run('list-sessions')


def test_writer_conflict_and_failed_turn(client, tmp_path):
    from core.session_persistence import resume_session_file
    from session import Session
    run, manager, calls = client
    sid = run('session', 'new')
    path = tmp_path / f'{sid}.ims.json'
    cfg = manager.create_session_config()
    reader = Session(cfg, ComponentRegistry(cfg))
    resume_session_file(reader, str(path))
    initial_builds = len(calls)
    original = path.read_bytes()
    try:
        failure = json.loads(run('session', 'send', sid, 'busy', '--json', code=1))
        assert 'in use' in failure['error']
        assert path.read_bytes() == original and len(calls) == initial_builds
        assert run('session', 'read', sid, '--json') == '[]'
    finally:
        reader.close()
    with session_lock(path):
        run('chat', '--resume', sid, code=1)
    failure = json.loads(run('session', 'send', sid, 'fail', '--json', code=1))
    assert failure['status'] == 'failed' and 'offline provider failure' in failure['error']
    assert 'fail' in run('session', 'read', sid)
    assert run('session', 'send', sid, 'next') == 'chat saw fail / next'


def test_picker_includes_other_origins_on_request(client):
    run, manager, _ = client
    sid = run('session', 'new')
    run('session', 'send', sid, 'Programmatic opening')
    output = run('chat', '--resume', stdin='1\n1\nA human follow-up\n/save session\n/quit\n')
    assert 'Show all sessions' in output
    assert 'chat saw Programmatic opening / A human follow-up' in output
    assert json.loads(run('session', 'list', '--json')) == []
    assert latest_session_path(manager.create_session_config()) == ''


def test_checkpoint_and_private_save(client, tmp_path):
    run, _, _ = client
    run('chat', stdin='Original\n/save checkpoint Template\n/quit\n')
    item, = json.loads(run('session', 'list', '--json'))
    path = tmp_path / f"{item['id']}.ims.json"
    original = path.read_bytes()
    assert 'Original' in run('session', 'read', item['id'])
    rejected = json.loads(run('session', 'send', item['id'], 'Change', '--json', code=1))
    assert 'checkpoint' in rejected['error'] and path.read_bytes() == original
    run('chat', '--private', stdin='Private question\n/save session\n/quit\n')
    assert len(json.loads(run('session', 'list', '--all', '--json'))) == 1


def test_failed_save_keeps_previous_transcript(client, tmp_path, monkeypatch):
    run, _, _ = client
    sid = run('session', 'new')
    path = tmp_path / f'{sid}.ims.json'
    before = path.read_bytes()

    def interrupted(data, stream, **kwargs):
        stream.write('{')
        raise OSError('disk full')

    monkeypatch.setattr(json, 'dump', interrupted)
    failure = json.loads(run('session', 'send', sid, 'hello', '--json', code=1))
    assert failure['status'] == 'failed' and 'disk full' in failure['error']
    assert path.read_bytes() == before
    assert set(p.name for p in tmp_path.iterdir()) == {path.name, '.locks'}


def test_unattended_confirmation_returns_and_records_tool_result(client, tmp_path, monkeypatch):
    run, manager, _ = client
    manager.base_config['TOOLS'] = {'active_tools': 'file', 'tool_mode': 'official'}
    target = tmp_path / 'note.txt'
    target.write_text('Original')

    class RequestWrite:
        def __init__(self, session):
            pass

        def chat(self):
            return ''

        def get_tool_calls(self):
            return [{'id': 'call-1', 'name': 'file', 'arguments': {
                'mode': 'write', 'file': 'note.txt', 'content': 'Changed'}}]

    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda *args: RequestWrite)
    sid = run('--base-dir', str(tmp_path), 'session', 'new')
    outcome = json.loads(run('session', 'send', sid, 'Edit the note', '--json', code=1))
    assert outcome['status'] == 'needs_input'
    assert target.read_text() == 'Original'
    row, = json.loads(run('session', 'read', sid, '--last', '1', '--json'))
    assert row['role'] == 'tool' and json.loads(row['text'])['needs_input']
