"""Workspace call facts, configurable prompt policy, and portable history access."""
from dataclasses import replace
import json
import time

import pytest

from app.project import resolve_project
from component_registry import ComponentRegistry
from workspace.experiment import ExecutionProfile, Strategy
from workspace.history import TaskHistory
from workspace.state import WorkspaceStore, read_json
from workspace.task_service import TaskService


def service_for(builder, tmp_path, *, calls=2, strategy=None, task_id='task-test', store=None):
    store = store or WorkspaceStore(tmp_path / 'records')
    run = store.create_run('Brief', {'kind': 'task', 'task_id': task_id})
    return TaskService(store, run['id'], builder, resolve_project(tmp_path),
                       ExecutionProfile(model='worker-model', tools=(), calls=calls),
                       'Work on the task.', strategy=strategy)


def finish(service, agent_id):
    assert service._agents[agent_id]['done'].wait(5)
    return service.status(agent_id)['agents'][0]


def test_call_facts_and_final_prompt_use_actual_shared_allowance(builder, tmp_path, monkeypatch):
    prompts = []
    class Provider:
        def __init__(self, session):
            self.session, self.calls, self.tools = session, 0, []
        def chat(self):
            self.calls += 1
            chat = self.session.get_context('chat').get('all')
            contexts = [item['context'].get()['content'] for turn in chat
                        for item in turn.get('context', [])]
            prompts.append(contexts)
            if self.calls == 1:
                self.tools = [{'id': 'status-1', 'name': 'agents',
                               'arguments': {'operation': 'status'}}]
                return ''
            self.tools = []
            return 'A bounded handoff report'
        def get_tool_calls(self):
            return self.tools
    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda *args: Provider)
    builder.config_manager.base_config['DEFAULT']['template_handler'] = 'prompt_template'
    strategy = Strategy(call_prompt='Call {{turn:workspace_provider_call}}',
                        final_call_prompt='Prepare a handoff; remaining={{turn:workspace_calls_remaining_after_current}}')
    service = service_for(builder, tmp_path, strategy=strategy)
    try:
        agent = service.start('Brief')
        assert finish(service, agent)['status'] == 'completed'
        assert 'Call 1' in prompts[0]
        assert not any('Prepare a handoff' in text for text in prompts[0])
        assert 'Call 2' in prompts[1]
        assert 'Prepare a handoff; remaining=0' in prompts[1]
        assert service.status()['calls_used'] == 2
        snapshot = read_json(service.store.run_dir(service.run_id) / agent / 'history.json')
        assert snapshot['phase'] == 'run_finished'
        rows = snapshot['messages']
        assert any(row['role'] == 'tool' and row['tool_call_id'] == 'status-1' for row in rows)
        assert rows[-1]['message'] == 'A bounded handoff report'
        assert any(row.get('tool_calls') for row in rows)
    finally:
        service.close()


def test_history_is_scoped_to_task_and_survives_service_close(builder, tmp_path):
    first = service_for(builder, tmp_path)
    agent = first.start('Previous attempt evidence')
    finish(first, agent)
    first.close()
    second = service_for(builder, tmp_path, store=first.store)
    foreign = service_for(builder, tmp_path, task_id='task-other', store=first.store)
    try:
        current = second.start('Continue explicitly')
        finish(second, current)
        other = foreign.start('Unrelated private content')
        finish(foreign, other)
        entries = second.history.list()['histories']
        assert {entry['run_id'] for entry in entries} == {first.run_id, second.run_id}
        result = second.history.read(agent, run_id=first.run_id, start=0)
        assert 'Previous attempt evidence' in result['content']
        with pytest.raises(ValueError, match='limited'):
            second.history.read(other, run_id=foreign.run_id)
        with pytest.raises(ValueError):
            second.history.read('../outside')
        live_chat = second._agents[current]['host'].session.get_context('chat').get('all')
        assert 'Previous attempt evidence' not in str(live_chat)
        action = second._agents[current]['host'].session.get_action('assistant_workspace_agents_tool')
        assert action.run({'operation': 'history_read', 'run_id': first.run_id,
                           'target': agent, 'start': 0}).payload['ok']
    finally:
        second.close()
        foreign.close()


def test_history_long_message_pagination_preserves_full_text(builder, tmp_path):
    service = service_for(builder, tmp_path)
    try:
        agent = service.start('Evidence ' + 'x' * 1500)
        finish(service, agent)
        parts, offset = [], 0
        while True:
            page = service.history.read(agent, start=0, limit=1, max_chars=256, offset=offset)
            assert len(page['content']) <= 256
            parts.append(page['content'])
            if page['next_offset'] is None:
                break
            offset = page['next_offset']
        rows = json.loads(''.join(parts))
        assert rows[0]['message'] == 'Evidence ' + 'x' * 1500
        with pytest.raises(ValueError):
            service.history.read(agent, max_chars=100000)
    finally:
        service.close()


def test_history_omits_opaque_provider_state_and_binary_context(builder, tmp_path):
    service = service_for(builder, tmp_path)
    try:
        agent = service.start('Brief')
        finish(service, agent)
        session = service._agents[agent]['host'].session
        session.get_context('chat').get('all')[-1].update(
            native_continuation={'opaque': 'private-provider-state'}, reasoning_content='private-reasoning')
        service.history.save(session, agent, 1, 'test')
        text = service.history.read(agent, start=0)['content']
        assert 'private-provider-state' not in text
        assert 'private-reasoning' not in text
    finally:
        service.close()


def test_archive_failure_cancels_instead_of_silently_losing_history(builder, tmp_path, monkeypatch):
    service = service_for(builder, tmp_path)
    def fail(*args, **kwargs):
        raise OSError('archive unavailable')
    monkeypatch.setattr(service.history, 'save', fail)
    try:
        agent = service.start('Brief')
        state = finish(service, agent)
        assert state['status'] != 'completed'
        assert service.cancellation.is_cancelled()
        assert 'archive unavailable' in service._persistence_error
    finally:
        service.close()


def test_strategy_lifecycle_options_are_policy_not_authority(tmp_path):
    from workspace.experiment import load_strategy
    path = tmp_path / 'strategy.toml'
    path.write_text('name="Experiment"\nfinal_call_prompt="Record what remains"\nfinal_call_threshold=2\n')
    strategy, _ = load_strategy(path)
    assert strategy.final_call_threshold == 2
    with pytest.raises(ValueError):
        replace(strategy, final_call_threshold=0).validate()
    with pytest.raises(ValueError):
        replace(strategy, workflow='fixed').validate()


def test_active_history_pagination_detects_changed_snapshot(builder, tmp_path):
    service = service_for(builder, tmp_path)
    try:
        agent = service.start('Brief')
        finish(service, agent)
        page = service.history.read(agent, start=0, max_chars=256)
        session = service._agents[agent]['host'].session
        session.get_context('chat').add('A later user message')
        service.history.save(session, agent, 2, 'context')
        with pytest.raises(ValueError, match='changed'):
            service.history.read(agent, start=0, offset=256, snapshot_id=page['snapshot_id'])
    finally:
        service.close()
