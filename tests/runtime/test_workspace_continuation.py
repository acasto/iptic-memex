"""Stop/continue semantics through application assembly and the actual tool executor."""
import time

import pytest

from app.project import resolve_project
from component_registry import ComponentRegistry
from workspace.continuation import prepare
from workspace.experiment import ExecutionProfile, Strategy, snapshot
from workspace.state import WorkspaceStore, read_json
from workspace.task_runner import TaskRunner
from workspace.task_worker import run_session


class YieldProvider:
    calls = 0

    def __init__(self, session):
        self.session = session
        self.tools = []

    def chat(self):
        type(self).calls += 1
        chat = self.session.get_context('chat').get('all')
        context = str([entry['context'].get() for turn in chat
                       for entry in turn.get('context', [])])
        if 'Use blue' in context:
            assert 'Measured width: 42' in context
            assert 'task-continuation' in context
            return 'Finished using blue; measured width is 42.'
        self.tools = [
            {'id': 'yield-1', 'name': 'agents', 'arguments': {
                'operation': 'yield', 'message': 'Which color?',
                'progress': 'Measured width: 42. Color is unresolved. No side effects.'}},
            {'id': 'must-skip', 'name': 'agents', 'arguments': {
                'operation': 'spawn', 'message': 'This must never execute'}},
        ]
        return ''

    def get_tool_calls(self):
        return self.tools


def test_yield_and_continue_preserve_work_without_replaying_tools(builder, tmp_path, monkeypatch):
    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda *_: YieldProvider)
    YieldProvider.calls = 0
    store = WorkspaceStore(tmp_path / 'records')
    runner = TaskRunner(store)
    task = runner.create('Choose the color after measuring.', resolve_project(tmp_path),
                         dict(snapshot(Strategy(), ExecutionProfile(model='worker-model')),
                              user_input=True))
    run = store.create_run(task['brief'], {'task_id': task['id']})
    spec = dict(task['spec'], brief=task['brief'], run_id=run['id'],
                deadline_monotonic=time.monotonic() + 10)
    result = run_session(spec, store, builder)
    assert result['status'] == 'needs_input', result
    assert result['error'] is None
    assert YieldProvider.calls == 1
    assert len(result['agents']) == 1
    history = read_json(store.run_dir(run['id']) / 'agent-1' / 'history.json')
    tool_rows = [r for r in history['messages'] if r['role'] == 'tool']
    assert len(tool_rows) == 2
    assert 'skipped' in tool_rows[-1]['message']
    task.update(status='needs_input', attempts=[{'run_id': run['id'], 'status': 'needs_input'}])
    runner.tasks.save(task)
    continuation = prepare(store, task, run['id'], 'Use blue')
    second = store.create_run(task['brief'], {'task_id': task['id']})
    resumed = run_session(dict(spec, run_id=second['id'], continuation=continuation), store, builder)
    assert resumed['status'] == 'completed', resumed
    assert 'width is 42' in resumed['answer']
    assert YieldProvider.calls == 2
    assert resumed['calls_used'] == 1


def test_yield_restrictions_and_persistence_failure(builder, tmp_path, monkeypatch):
    from workspace.task_service import TaskService
    service = TaskService(WorkspaceStore(tmp_path / 'records'), 'run-test', builder,
                          resolve_project(tmp_path), ExecutionProfile(model='worker-model'), 'Work')
    service._agents['root'] = {'parent_id': None}
    service._agents['child'] = {'parent_id': 'root'}
    with pytest.raises(ValueError, match='root'):
        service.yield_to_user('child', 'Question', 'Progress')
    with pytest.raises(ValueError, match='input-enabled'):
        service.yield_to_user('root', 'Question', 'Progress')
    service.user_input = True
    with pytest.raises(ValueError, match='progress'):
        service.yield_to_user('root', 'Question', '')
    service._agents['root'].update(host=type('Host', (), {'session': object()})(), generations=1)
    def fail(*args):
        raise OSError('Disk unavailable')
    monkeypatch.setattr(service.history, 'save', fail)
    with pytest.raises(OSError):
        service.yield_to_user('root', 'Question', 'Progress')
    assert service.cancellation.is_cancelled()
    assert service.input_needed is None
    assert service._persistence_error == 'Disk unavailable'


def test_chat_carries_custom_config_through_compatibility_builder(builder, tmp_path):
    from modes.chat_mode import ChatMode
    from session import SessionBuilder as CompatibilityBuilder
    builder.config_manager.config_file = str(tmp_path / 'custom.ini')
    facade = CompatibilityBuilder(builder.config_manager)
    session = facade.build(mode='internal', project_context=resolve_project(tmp_path))
    try:
        ChatMode(session, facade)
        assert session.chat_config_file == str(tmp_path / 'custom.ini')
    finally:
        session.close()
