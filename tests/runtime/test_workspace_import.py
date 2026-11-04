"""Explicit saved-result import through the normal chat context pipeline."""
import pytest

from app.chat import run_chat_turn
from app.project import resolve_project
from workspace.chat import ChatWorkspace
from workspace.state import write_json


@pytest.fixture
def imported_task(builder, tmp_path):
    session = builder.build(mode='internal', project_context=resolve_project(tmp_path))
    attachment = ChatWorkspace(session, tmp_path / 'records')
    task = attachment.tasks.create('Find the evidence', {})
    run = attachment.store.create_run(task['brief'], {'task_id': task['id']})
    task.update(status='partial', attempts=[{'run_id': run['id'], 'status': 'partial'}])
    attachment.tasks.save(task)
    result = {'task_id': task['id'], 'run_id': run['id'], 'status': 'partial',
              'answer': 'Evidence from source.py:42', 'error': 'Budget exhausted'}
    path = attachment.store.run_dir(run['id']) / 'task-result.json'
    write_json(path, result)
    yield session, attachment, task, result, path
    session.close()


def test_import_is_explicit_idempotent_and_reaches_next_turn(imported_task):
    session, attachment, task, result, path = imported_task
    assert not session.get_contexts('assistant')
    attachment.inspect(task['id'])
    attachment.notifications()
    assert not session.get_contexts('assistant')
    calls = session.provider.calls
    attachment.import_result(task['id'])
    assert session.provider.calls == calls
    assert 'already' in attachment.import_result(task['id'])
    contexts = session.get_contexts('assistant')
    assert len(contexts) == 1
    text = contexts[0].get()['content']
    for expected in ('unverified', task['id'], result['run_id'], 'partial',
                     'Budget exhausted', str(path), result['answer']):
        assert expected in text
    session.set_flag('hooks_disabled', True)
    outcome = run_chat_turn(session, 'Assess the evidence')
    assert outcome.status == 'completed'
    messages = session.get_context('chat').get('all')
    user = next(message for message in messages if message['role'] == 'user')
    assert any(item['context'].get().get('content') == text for item in user['context'])


@pytest.mark.parametrize('change', ['running', 'missing', 'empty', 'wrong_id', 'oversized'])
def test_import_rejects_unavailable_or_mismatched_answers(imported_task, change):
    session, attachment, task, result, path = imported_task
    if change == 'running':
        task['attempts'][0]['status'] = 'running'
        attachment.tasks.save(task)
    elif change == 'missing':
        path.unlink()
    else:
        result.update({'answer': ''} if change == 'empty' else
                      {'task_id': 'task-other'} if change == 'wrong_id' else
                      {'answer': 'a' * 32001})
        write_json(path, result)
    with pytest.raises(ValueError):
        attachment.import_result(task['id'])
    assert not session.get_contexts('assistant')


def test_import_can_select_earlier_attempt_but_not_foreign_attempt(imported_task):
    session, attachment, task, result, path = imported_task
    task['attempts'].append({'run_id': 'run-new', 'status': 'running'})
    task['status'] = 'running'
    attachment.tasks.save(task)
    with pytest.raises(ValueError, match='finish'):
        attachment.import_result(task['id'])
    with pytest.raises(ValueError, match='belong'):
        attachment.import_result(task['id'], 'run-foreign')
    attachment.import_result(task['id'], result['run_id'])
    assert len(session.get_contexts('assistant')) == 1
