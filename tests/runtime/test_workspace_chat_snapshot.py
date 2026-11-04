"""Explicit conversation selection and delivery to isolated task sessions."""
from dataclasses import asdict
import time

import pytest

from app.project import resolve_project
from workspace.chat import ChatWorkspace
from workspace.chat_snapshot import select_messages
from workspace.experiment import ExecutionProfile, Strategy
from workspace.state import WorkspaceStore
from workspace.task_runner import TaskRunner
from workspace.task_worker import run_session


def test_snapshot_is_opt_in_and_saved_as_independent_text(builder, tmp_path, monkeypatch):
    session = builder.build(mode='internal', project_context=resolve_project(tmp_path))
    monkeypatch.setattr(TaskRunner, 'run', lambda *a, **kw: None)
    try:
        chat = session.get_context('chat') or session.add_context('chat')
        chat.add('Earlier topic', role='user')
        chat.add('Selected question', role='user', context=[{'private': 'attachment'}])
        chat.add('tool secret', role='tool')
        chat.add('', role='assistant', extra={'tool_calls': [{'secret': 'arguments'}]})
        chat.add('Selected answer', role='assistant')
        attachment = ChatWorkspace(session, tmp_path / 'records')
        plain = attachment.start('Investigate')
        assert 'chat_snapshot' not in attachment.tasks.read(plain)['spec']
        selected = attachment.start('Investigate', chat_last=2)
        saved = attachment.tasks.read(selected)
        assert saved['brief'] == 'Investigate'
        assert saved['spec']['chat_snapshot']['messages'] == [
            {'role': 'user', 'text': 'Selected question'},
            {'role': 'assistant', 'text': 'Selected answer'}]
        chat.get('all')[-1]['message'] = 'Changed later'
        assert attachment.tasks.read(selected)['spec']['chat_snapshot']['messages'][-1][
            'text'] == 'Selected answer'
        attachment.close()
    finally:
        session.close()


@pytest.mark.parametrize('count', [0, -1, True, 101, '2'])
def test_invalid_selection_rejected(builder, count):
    session = builder.build(mode='internal')
    try:
        with pytest.raises(ValueError):
            select_messages(session, count)
    finally:
        session.close()


def test_empty_and_oversized_selection_rejected(builder):
    session = builder.build(mode='internal')
    try:
        with pytest.raises(ValueError, match='no user'):
            select_messages(session, 1)
        chat = session.get_context('chat') or session.add_context('chat')
        chat.add('x' * 16001)
        with pytest.raises(ValueError, match='fewer'):
            select_messages(session, 1)
    finally:
        session.close()


def test_worker_receives_snapshot_as_context(builder, tmp_path, monkeypatch):
    from component_registry import ComponentRegistry
    received = []
    class Provider:
        def __init__(self, session):
            self.session = session
        def chat(self):
            turn = self.session.get_context('chat').get('all')[0]
            received.append(turn)
            return 'Used the selected background'
    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda *args: Provider)
    store = WorkspaceStore(tmp_path / 'records')
    run = store.create_run('Investigate', {})
    project = asdict(resolve_project(tmp_path))
    project['root'] = str(project['root'])
    spec = {'profile': asdict(ExecutionProfile(model='worker-model', calls=1)),
            'strategy': asdict(Strategy()), 'project': project, 'run_id': run['id'],
            'brief': 'Investigate', 'deadline_monotonic': time.monotonic() + 5,
            'chat_snapshot': {'version': 1, 'messages': [
                {'role': 'user', 'text': 'The timeout occurs after the second attempt'}]}}
    result = run_session(spec, store, builder)
    assert result['status'] == 'completed', result
    assert received[0]['message'] == 'Investigate'
    text = '\n'.join(item['context'].get().get('content', '')
                     for item in received[0]['context'])
    assert 'historical text' in text
    assert 'second attempt' in text
