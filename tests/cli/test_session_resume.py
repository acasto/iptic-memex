from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import main
from actions.manage_sessions_action import ManageSessionsAction
from base_classes import InteractionNeeded
from component_registry import ComponentRegistry
from config_manager import ConfigManager
from core.null_ui import NullUI
from core.session_persistence import list_sessions, load_session_data, save_session
from session import Session
from ui.cli import CLIUI


@pytest.fixture
def resume_session(tmp_path, monkeypatch):
    config_manager = ConfigManager()
    config = config_manager.create_session_config()
    session = Session(config, ComponentRegistry(config))
    session.add_context('chat')
    original_get_option = session.get_option

    def get_option(section, key, fallback=None):
        if section == 'SESSIONS' and key == 'session_directory':
            return str(tmp_path)
        return original_get_option(section, key, fallback=fallback)

    monkeypatch.setattr(session, 'get_option', get_option)
    session.ui = CLIUI(session)
    monkeypatch.setattr(session.utils.output, 'write', lambda message='', **_kw: click.echo(message))
    import modes.chat_mode
    monkeypatch.setattr(main, 'ConfigManager', lambda _conf=None: config_manager)
    builder = SimpleNamespace(build=lambda *_args, **_kwargs: session)
    monkeypatch.setattr(main, 'SessionBuilder', lambda _config: builder)
    monkeypatch.setattr(modes.chat_mode, 'ChatMode', lambda *_args: SimpleNamespace(start=lambda: None))
    return session


def write_session(tmp_path, sid, updated, *, kind='session'):
    path = tmp_path / f'{sid}.ims.json'
    path.write_text(json.dumps({
        'id': sid, 'kind': kind, 'created': 10, 'updated': updated,
        'params': {'model': 'test-model'},
        'chat': [
            {'role': 'user', 'message': sid + ' opening ' + 'long text ' * 40},
            {'role': 'assistant', 'message': 'reply'},
            {'role': 'tool', 'message': 'tool output'},
            {'role': 'user', 'message': sid + '\nlatest\t' + 'long text ' * 40},
        ],
    }))
    return path


@pytest.mark.parametrize('flags, expected', [
    (['--resume'], 'older'),
    (['--resume', '--latest'], 'newer'),
    (['--resume', '--last'], 'newer'),
    (['--last', '--resume'], 'newer'),
    (['--resume', 'older'], 'older'),
    (['--resume=older'], 'older'),
    (['--resume', 'PATH'], 'older'),
])
def test_cli_resume_modes(resume_session, tmp_path, monkeypatch, flags, expected):
    write_session(tmp_path, 'newer', 200)
    # Write older second, so latest cannot simply be based on file mtime.
    write_session(tmp_path, 'older', 100)
    if flags == ['--resume', 'PATH']:
        flags = ['--resume', str(tmp_path / 'older.ims.json')]
    prompts = []

    def get_input(prompt=None, **_kwargs):
        prompts.append(prompt)
        return '2'

    monkeypatch.setattr(resume_session.utils.input, 'get_input', get_input)
    result = CliRunner().invoke(main.cli, ['chat', *flags])
    assert result.exit_code == 0, result.output
    assert resume_session.session_uid == expected
    if flags == ['--resume']:
        assert len(prompts) == 1
        assert '2 turns' in result.output
        assert 'Started: newer opening' in result.output
        assert 'Latest: older latest' in result.output
        assert 'long text ' * 10 not in result.output
    else:
        assert not prompts


@pytest.mark.parametrize('flags', [
    ['--last'], ['--latest'], ['--resume', 'older', '--latest'],
])
def test_cli_rejects_conflicting_resume_flags(resume_session, flags):
    result = CliRunner().invoke(main.cli, ['chat', *flags])
    assert result.exit_code == 2
    assert '--latest/--last' in result.output


def test_slash_command_picker_and_cancel(resume_session, tmp_path, monkeypatch):
    write_session(tmp_path, 'saved', 100)
    original_id = resume_session.session_uid
    # Blank/invalid CLI choices default to Cancel, never to the first session.
    monkeypatch.setattr(resume_session.utils.input, 'get_input', lambda *_args, **_kwargs: '')
    assert resume_session.get_action('chat_commands').run('/load session') is True
    assert resume_session.session_uid == original_id
    monkeypatch.setattr(resume_session.utils.input, 'get_input', lambda *_args, **_kwargs: '1')
    assert resume_session.get_action('chat_commands').run('/load session') is True
    assert resume_session.session_uid == 'saved'


def test_empty_picker_does_not_prompt(resume_session):
    resume_session.ui = NullUI()
    result = ManageSessionsAction(resume_session).run(['resume'])
    assert result.payload['error'] == 'not_found'
    assert resume_session.ui.events[0]['message'] == 'No saved sessions found.'


def test_nonblocking_picker_keeps_selection_snapshot(resume_session, tmp_path):
    path = write_session(tmp_path, 'checkpoint', 100, kind='checkpoint')
    resume_session.ui = NullUI()
    with pytest.raises(InteractionNeeded) as need:
        ManageSessionsAction(resume_session).run(['resume'])
    choice = need.value.spec['options'][0]
    write_session(tmp_path, 'new-session', 200)
    # Web recreates the action and passes the response in a wrapper.
    result = ManageSessionsAction(resume_session).resume('web-token', {'response': choice})
    assert result.payload['path'] == str(path)
    assert result.payload['forked'] is True
    assert resume_session.session_uid != 'checkpoint'
    assert resume_session.get_context('chat').get('all')[0]['message'].startswith('checkpoint')


def test_disappearing_selection_is_reported(resume_session, tmp_path):
    path = write_session(tmp_path, 'saved', 100)
    resume_session.ui = NullUI()
    with pytest.raises(InteractionNeeded) as need:
        ManageSessionsAction(resume_session).run(['resume'])
    path.unlink()
    result = ManageSessionsAction(resume_session).resume('token', need.value.spec['options'][0])
    assert result.payload['error'] == 'not_found'


def test_list_sessions_is_bounded_and_counts_user_inputs(resume_session, tmp_path):
    path = write_session(tmp_path, 'saved', 100)
    data = json.loads(path.read_text())
    data['title'] = 'very long title ' * 40
    data['chat'].extend([
        {'role': 'user', 'message': '', 'meta': {'auto_submit': True}},
        {'role': 'user', 'message': '', 'context': [{'type': 'image'}]},
    ])
    path.write_text(json.dumps(data))
    (tmp_path / 'invalid.ims.json').write_text('[]')
    item, = list_sessions(resume_session)
    assert item['turn_count'] == 3
    assert len(item['first_user']) <= 80
    assert item['first_user'].endswith('...')
    assert item['last_user'] == ''
    resume_session.ui = NullUI()
    result = ManageSessionsAction(resume_session).run(['list'])
    assert result.payload['ok'] is True
    output = resume_session.ui.events[-1]['message']
    assert '3 turns' in output
    assert 'Latest: (no user text)' in output
    assert 'very long title ' * 6 not in output


def test_save_and_resume_preserve_creation_time_and_title(resume_session, tmp_path, monkeypatch):
    import core.session_persistence as persistence
    resume_session.get_context('chat').add('hello', role='user')
    monkeypatch.setattr(persistence, '_now_ts', lambda: 100.0)
    path = save_session(resume_session, title='A useful name', directory=str(tmp_path))
    monkeypatch.setattr(persistence, '_now_ts', lambda: 150.0)
    checkpoint = save_session(resume_session, kind='checkpoint', title='Template',
                              directory=str(tmp_path))
    assert load_session_data(checkpoint)['created'] == 150.0
    monkeypatch.setattr(persistence, '_now_ts', lambda: 200.0)
    save_session(resume_session, directory=str(tmp_path))
    data = load_session_data(path)
    assert data['created'] == 100.0
    assert data['updated'] == 200.0
    assert data['title'] == 'A useful name'
    resume_session.set_user_data('__session_created__', 999)
    resume_session.set_user_data('__session_title__', 'another title')
    ManageSessionsAction(resume_session).run(['resume', path])
    monkeypatch.setattr(persistence, '_now_ts', lambda: 300.0)
    save_session(resume_session, directory=str(tmp_path))
    data = load_session_data(path)
    assert data['created'] == 100.0
    assert data['updated'] == 300.0
    assert data['title'] == 'A useful name'
