from __future__ import annotations

from types import SimpleNamespace

from core.mode_runner import ModeResult
from core.workspace_state import WorkspaceStore
from core.workspace_worker import execute


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
        kwargs['on_event']('step_start', {'step': 1, 'max_steps': 3})
        assert not session.context
        note = store.publish(task['run_id'], 'worker-2', 'Peer result', 'New evidence')
        kwargs['on_event']('step_start', {'step': 2, 'max_steps': 3})
        assert note['id'] in session.context[-1][1]['content']
        return ModeResult('Grounded answer', None, 2, None, None, [], stop_reason='sentinel')

    monkeypatch.setattr(worker, 'run_agent', run_agent)
    result = execute(task)
    assert result['status'] == 'completed'
    assert session.closed
    assert len(store.notes()) == 2


def test_synthesis_uses_completion_and_receives_no_worker_chat(tmp_path, monkeypatch):
    store, task = spec(tmp_path, phase='synthesis')
    session = Session()
    worker = setup_builder(monkeypatch, session)

    def complete(**kwargs):
        assert kwargs == {'session': session, 'message': 'Question'}
        assert not session.context
        return ModeResult('Final answer', None, 1, None, None, [])

    monkeypatch.setattr(worker, 'run_completion', complete)
    assert execute(task)['last_text'] == 'Final answer'
    assert session.closed
    assert not store.notes()


def test_exhausted_worker_budget_is_incomplete(tmp_path, monkeypatch):
    store, task = spec(tmp_path)
    session = Session()
    worker = setup_builder(monkeypatch, session)
    monkeypatch.setattr(worker, 'run_agent', lambda **kw: ModeResult(
        'Partial evidence', None, 3, None, None, [], stop_reason='steps'))
    assert execute(task)['status'] == 'incomplete'


def test_provider_error_is_not_published_as_a_finding(tmp_path, monkeypatch):
    store, task = spec(tmp_path)
    session = Session()
    worker = setup_builder(monkeypatch, session)
    monkeypatch.setattr(worker, 'run_agent', lambda **kw: ModeResult(
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
