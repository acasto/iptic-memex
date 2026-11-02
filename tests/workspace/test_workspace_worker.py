from __future__ import annotations

from types import SimpleNamespace

from core.runtime import RunOutcome
from core.workspace_state import WorkspaceStore
from core.workspace_worker import execute


def outcome(text, raw=None, turns=0, cost=None, usage=None, events=None, **kwargs):
    return RunOutcome(run_id='fixture', last_text=text, raw=raw, turns=turns,
                      cost=cost, usage=usage, events=events or [], **kwargs)


class Session:
    def __init__(self):
        self.context = []
        self.data = {}
        self.closed = False
        self.flags = {}

    def set_flag(self, key, value):
        self.flags[key] = value

    def set_user_data(self, key, value):
        self.data[key] = value

    def get_user_data(self, key):
        return self.data.get(key)

    def get_params(self):
        return {'model': 'local-test'}

    def get_context(self, _kind):
        return SimpleNamespace(get=lambda: {'content': 'Resolved system prompt'})

    def enter_agent_mode(self, policy):
        self.policy = policy

    def get_action(self, name):
        return SimpleNamespace(commands={'file': {}})

    def set_option(self, key, value):
        self.data[key] = value

    def add_context(self, kind, data):
        self.context.append((kind, data))

    def handle_exit(self, confirm=False):
        self.closed = True

    def close(self):
        self.closed = True


def spec(tmp_path, phase='worker'):
    store = WorkspaceStore(tmp_path / 'workspace')
    run = store.create_run('Question', {})
    return store, dict(directory=str(store.root), repository=str(tmp_path), run_id=run['id'],
                       worker='worker-1', model='local-test', prompt='Test prompt',
                       message='Question', question='Question', steps=3,
                       writes_policy='deny', phase=phase)


def setup_builder(monkeypatch, session):
    import core.workspace_worker as worker

    class Builder:
        def __init__(self, config):
            self.config = config

        def build(self, **kwargs):
            assert self.config.base_config.get('MEMORY', 'active') == 'false'
            return session

    monkeypatch.setattr(worker, 'SessionBuilder', Builder)
    return worker


def test_new_peer_notes_enter_context_between_steps_and_cleanup_runs(tmp_path, monkeypatch):
    store, task = spec(tmp_path)
    session = Session()
    worker = setup_builder(monkeypatch, session)

    def run_agent(**kwargs):
        assert kwargs['disable_hooks'] is True
        assert kwargs['writes_policy'] == 'deny'
        assert session.data['active_tools_agent'] == 'file,workspace'
        kwargs['before_call'](session, 1)
        assert not session.context
        note = store.publish(task['run_id'], 'worker-2', 'Peer result', 'New evidence')
        kwargs['before_call'](session, 2)
        assert note['id'] in session.context[-1][1]['content']
        return outcome('Grounded answer', None, 2, None, None, [], stop_reason='sentinel')

    monkeypatch.setattr(worker, 'run_agent', run_agent)
    result = execute(task)
    assert result['status'] == 'completed'
    assert session.closed
    assert len(store.notes()) == 2
    assert store.notes(run_id=task['run_id'])[0]['kind'] == 'summary'
    assert (store.run_dir(task['run_id']) / 'worker-1' / 'system-prompt.md').exists()


def test_synthesis_uses_completion_and_receives_no_worker_chat(tmp_path, monkeypatch):
    store, task = spec(tmp_path, phase='synthesis')
    session = Session()
    worker = setup_builder(monkeypatch, session)

    def complete(**kwargs):
        assert kwargs == {'session': session, 'message': 'Question'}
        assert not session.context
        return outcome('Final answer', None, 1, None, None, [])

    monkeypatch.setattr(worker, 'run_completion', complete)
    assert execute(task)['last_text'] == 'Final answer'
    assert session.closed
    assert not store.notes()


def test_exhausted_worker_budget_is_incomplete(tmp_path, monkeypatch):
    store, task = spec(tmp_path)
    session = Session()
    worker = setup_builder(monkeypatch, session)
    monkeypatch.setattr(worker, 'run_agent', lambda **kw: outcome(
        'Partial evidence', None, 3, None, None, [], stop_reason='steps'))
    assert execute(task)['status'] == 'incomplete'


def test_final_summary_links_own_findings_and_explicit_peer_citations(tmp_path, monkeypatch):
    store, task = spec(tmp_path)
    session = Session()
    worker = setup_builder(monkeypatch, session)
    own = store.publish(task['run_id'], 'worker-1', 'My evidence', 'Own finding')
    peer = store.publish(task['run_id'], 'worker-2', 'Peer evidence', 'Peer finding')
    monkeypatch.setattr(worker, 'run_agent', lambda **kw: outcome(
        f"Answer building on {peer['id']}", None, 1, None, None, []))
    result = execute(task)
    final = store.read_note(result['note_id'])
    assert final['kind'] == 'summary'
    assert set(final['references']) == {own['id'], peer['id']}


def test_provider_error_is_not_published_as_a_finding(tmp_path, monkeypatch):
    store, task = spec(tmp_path)
    session = Session()
    worker = setup_builder(monkeypatch, session)
    monkeypatch.setattr(worker, 'run_agent', lambda **kw: outcome(
        'Provider connection error', None, 1, None, None, [], status='failed',
        error='Provider connection error', stop_reason='error'))
    assert execute(task)['status'] == 'failed'
    assert not store.notes()
    assert session.closed


def test_workspace_tool_is_gated_and_checks_contribution_dependencies(tmp_path):
    from actions.assistant_workspace_tool_action import AssistantWorkspaceToolAction

    store, task = spec(tmp_path)
    session = Session()
    assert not AssistantWorkspaceToolAction.can_run(session)
    session.data['__workspace__'] = dict(directory=str(store.root), run_id=task['run_id'],
                                       worker='worker-1')
    assert AssistantWorkspaceToolAction.can_run(session)
    action = AssistantWorkspaceToolAction(session)
    result = action.run({'operation': 'publish', 'title': 'Evidence', 'content': 'A fact'})
    assert result.payload['ok']
    note = result.payload['result']
    assert action.run({'operation': 'read', 'note_id': note['id']}).payload['ok']
    assert not action.run({'operation': 'publish', 'title': 'Bad', 'content': 'A claim',
                           'references': 'missing'}).payload['ok']


def test_rejected_publication_keeps_diagnostics_without_saving_its_body(tmp_path):
    import json
    from actions.assistant_workspace_tool_action import AssistantWorkspaceToolAction

    store, task = spec(tmp_path)
    session = Session()
    session.data['__workspace__'] = dict(directory=str(store.root), run_id=task['run_id'],
                                       worker='worker-1')
    result = AssistantWorkspaceToolAction(session).run(
        {'operation': 'publish', 'title': 'Too long'}, 'x' * 12001)
    assert not result.payload['ok']
    path = store.run_dir(task['run_id']) / 'worker-1' / 'events.jsonl'
    failure = json.loads(path.read_text().splitlines()[-1])
    assert failure['event'] == 'workspace_error'
    assert failure['content_chars'] == 12001
    assert '12001' in failure['error']
    assert 'x' * 100 not in path.read_text()
    assert not store.notes()


def test_worker_sees_remaining_time_independently_of_turn_budget(tmp_path, monkeypatch):
    import time

    store, task = spec(tmp_path)
    task['deadline_monotonic'] = time.monotonic() + 30
    session = Session()
    worker = setup_builder(monkeypatch, session)

    def investigate(**kwargs):
        kwargs['before_call'](session, 1)
        budget = next(data for _, data in session.context if data['name'] == 'workspace_budget')
        assert 'seconds' in budget['content']
        assert 'independently' in budget['content']
        saved = store.snapshot(task['run_id'])['workers'][0]
        assert 0 < saved['time_remaining_seconds'] <= 30
        return outcome('Finding', None, 1, None, None, [])

    monkeypatch.setattr(worker, 'run_agent', investigate)
    assert execute(task)['status'] == 'completed'
