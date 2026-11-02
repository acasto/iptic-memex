from __future__ import annotations

from types import SimpleNamespace

from core.runtime import RunOutcome
from workspace.state import WorkspaceStore
from workspace.worker import execute
from workspace.tools import WorkspaceAccess


def outcome(text, raw=None, turns=0, cost=None, usage=None, events=None, **kwargs):
    return RunOutcome(run_id='fixture', last_text=text, raw=raw, turns=turns,
                      cost=cost, usage=usage, events=events or [], **kwargs)


class Session:
    def __init__(self):
        self.context = []
        self.closed = False

    def get_params(self):
        return {'model': 'local-test'}

    def get_context(self, _kind):
        return SimpleNamespace(get=lambda: {'content': 'Resolved system prompt'})

    def add_context(self, kind, data):
        self.context.append((kind, data))
        return SimpleNamespace(get=lambda: data)

    def close(self):
        self.closed = True


def spec(tmp_path, phase='worker'):
    store = WorkspaceStore(tmp_path / 'workspace')
    run = store.create_run('Question', {})
    return store, dict(directory=str(store.root), repository=str(tmp_path), run_id=run['id'],
                       worker='worker-1', model='local-test', prompt='Test prompt',
                       message='Question', question='Question', steps=3,
                       writes_policy='deny', phase=phase)


def setup_runtime(monkeypatch, session):
    import workspace.worker as worker

    def build(spec, store):
        if spec.get('phase') != 'synthesis':
            session.workspace = WorkspaceAccess(store, spec['run_id'], spec['worker'])
        return session

    monkeypatch.setattr(worker.runtime, 'build_session', build)
    return worker


def test_new_peer_notes_enter_context_between_steps_and_cleanup_runs(tmp_path, monkeypatch):
    store, task = spec(tmp_path)
    session = Session()
    worker = setup_runtime(monkeypatch, session)

    def run_agent(**kwargs):
        assert kwargs['disable_hooks'] is True
        assert kwargs['writes_policy'] == 'deny'
        kwargs['before_call'](session, 1)
        assert not session.context
        note = store.publish(task['run_id'], 'worker-2', 'Peer result', 'New evidence')
        kwargs['before_call'](session, 2)
        assert note['id'] in session.context[-1][1]['content']
        return outcome('Grounded answer', None, 2, None, None, [], stop_reason='sentinel')

    monkeypatch.setattr(worker.runtime, 'run_agent', run_agent)
    result = execute(task)
    assert result['status'] == 'completed'
    assert session.closed
    assert len(store.notes()) == 2
    assert store.notes(run_id=task['run_id'])[0]['kind'] == 'summary'
    assert (store.run_dir(task['run_id']) / 'worker-1' / 'system-prompt.md').exists()


def test_synthesis_uses_completion_and_receives_no_worker_chat(tmp_path, monkeypatch):
    store, task = spec(tmp_path, phase='synthesis')
    session = Session()
    worker = setup_runtime(monkeypatch, session)

    def complete(**kwargs):
        assert kwargs['session'] is session and kwargs['message'] == 'Question'
        assert callable(kwargs['on_event'])
        assert not session.context
        return outcome('Final answer', None, 1, None, None, [])

    monkeypatch.setattr(worker.runtime, 'run_completion', complete)
    assert execute(task)['last_text'] == 'Final answer'
    assert session.closed
    assert not store.notes()


def test_exhausted_worker_budget_is_incomplete(tmp_path, monkeypatch):
    store, task = spec(tmp_path)
    session = Session()
    worker = setup_runtime(monkeypatch, session)
    monkeypatch.setattr(worker.runtime, 'run_agent', lambda **kw: outcome(
        'Partial evidence', None, 3, None, None, [], stop_reason='steps'))
    assert execute(task)['status'] == 'incomplete'


def test_final_summary_links_own_findings_and_explicit_peer_citations(tmp_path, monkeypatch):
    store, task = spec(tmp_path)
    session = Session()
    worker = setup_runtime(monkeypatch, session)
    own = store.publish(task['run_id'], 'worker-1', 'My evidence', 'Own finding')
    peer = store.publish(task['run_id'], 'worker-2', 'Peer evidence', 'Peer finding')
    monkeypatch.setattr(worker.runtime, 'run_agent', lambda **kw: outcome(
        f"Answer building on {peer['id']}", None, 1, None, None, []))
    result = execute(task)
    final = store.read_note(result['note_id'])
    assert final['kind'] == 'summary'
    assert set(final['references']) == {own['id'], peer['id']}


def test_provider_error_is_not_published_as_a_finding(tmp_path, monkeypatch):
    store, task = spec(tmp_path)
    session = Session()
    worker = setup_runtime(monkeypatch, session)
    monkeypatch.setattr(worker.runtime, 'run_agent', lambda **kw: outcome(
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
    session.workspace = WorkspaceAccess(store, task['run_id'], 'worker-1')
    assert AssistantWorkspaceToolAction.can_run(session)
    action = AssistantWorkspaceToolAction(session)
    result = action.run({'operation': 'publish', 'title': 'Evidence', 'content': 'A fact'})
    assert result.payload['ok']
    note = result.payload['result']
    assert action.run({'operation': 'read', 'note_id': note['id']}).payload['ok']
    assert not action.run({'operation': 'publish', 'title': 'Bad', 'content': 'A claim',
                           'references': 'missing'}).payload['ok']


def test_rejected_publication_keeps_diagnostics_without_logging_its_body(tmp_path):
    import json
    from actions.assistant_workspace_tool_action import AssistantWorkspaceToolAction

    store, task = spec(tmp_path)
    session = Session()
    session.workspace = WorkspaceAccess(store, task['run_id'], 'worker-1')
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
    worker = setup_runtime(monkeypatch, session)

    def investigate(**kwargs):
        kwargs['before_call'](session, 1)
        budget = next(data for _, data in session.context if data['name'] == 'workspace_budget')
        assert 'seconds' in budget['content']
        assert 'independently' in budget['content']
        saved = store.snapshot(task['run_id'])['workers'][0]
        assert 0 < saved['time_remaining_seconds'] <= 30
        return outcome('Finding', None, 1, None, None, [])

    monkeypatch.setattr(worker.runtime, 'run_agent', investigate)
    assert execute(task)['status'] == 'completed'


def test_note_bursts_drain_in_bounded_batches_without_self_notes_or_duplicates(tmp_path, monkeypatch):
    import json

    store, task = spec(tmp_path)
    session = Session()
    worker = setup_runtime(monkeypatch, session)
    peers = {store.publish(task['run_id'], 'worker-2', f'Peer {i}', 'Peer evidence')['id']
             for i in range(19)}
    for i in range(10):
        store.publish(task['run_id'], 'worker-1', f'Own {i}', 'Own evidence')

    def investigate(**kwargs):
        sizes = []
        for step in range(1, 5):
            session.context.clear()
            kwargs['before_call'](session, step)
            update = [data for _, data in session.context if data['name'] == 'workspace_updates']
            sizes.append(len(json.loads(update[0]['content'].split('\n', 1)[1])) if update else 0)
        assert sizes == [8, 8, 3, 0]
        return outcome('Finding', turns=4)

    monkeypatch.setattr(worker.runtime, 'run_agent', investigate)
    assert execute(task)['status'] == 'completed'
    path = store.run_dir(task['run_id']) / 'worker-1' / 'events.jsonl'
    delivered = [note for line in path.read_text().splitlines()
                 if (event := json.loads(line))['event'] == 'notes_seen' for note in event['notes']]
    assert len(delivered) == len(set(delivered)) == 19
    assert set(delivered) == peers


def test_rejected_context_delivery_fails_visibly_without_claiming_notes_seen(tmp_path, monkeypatch):
    import json

    store, task = spec(tmp_path)
    session = Session()
    worker = setup_runtime(monkeypatch, session)
    store.publish(task['run_id'], 'worker-2', 'Peer', 'Evidence')
    session.add_context = lambda kind, data: None

    def investigate(**kwargs):
        kwargs['before_call'](session, 1)
        raise AssertionError('Rejected context was silently ignored')

    monkeypatch.setattr(worker.runtime, 'run_agent', investigate)
    result = execute(task)
    assert result['status'] == 'failed'
    assert 'attach shared workspace updates' in result['error']
    path = store.run_dir(task['run_id']) / 'worker-1' / 'events.jsonl'
    assert all(json.loads(line)['event'] != 'notes_seen' for line in path.read_text().splitlines())


def test_publication_remains_successful_when_its_reporting_fails(tmp_path, monkeypatch):
    from actions.assistant_workspace_tool_action import AssistantWorkspaceToolAction

    store, task = spec(tmp_path)
    session = Session()
    session.workspace = WorkspaceAccess(store, task['run_id'], 'worker-1')

    def fail_event(*args, **kwargs):
        raise OSError('Log unavailable')

    monkeypatch.setattr(store, 'event', fail_event)
    result = AssistantWorkspaceToolAction(session).run(
        {'operation': 'publish', 'title': 'Evidence', 'content': 'Saved once'})
    assert result.payload['ok']
    assert 'Note saved' in result.payload['warning']
    assert store.read_note(result.payload['result']['id'])['content'].strip() == 'Saved once'
    assert len(store.notes()) == 1


def test_publication_retry_is_idempotent_and_rejected_drafts_can_be_repaired(tmp_path):
    from actions.assistant_workspace_tool_action import AssistantWorkspaceToolAction

    store, task = spec(tmp_path)
    session = Session()
    session.workspace = WorkspaceAccess(store, task['run_id'], 'worker-1')
    action = AssistantWorkspaceToolAction(session)
    args = dict(operation='publish', title='Evidence', content='Original finding',
                publication_id='stable', sources='source.py:10 at revision test',
                method='Read the original source', uncertainty='Not exercised live')
    first = action.run(args).payload
    second = action.run(args).payload
    assert first['ok'] and second['ok']
    assert first['result']['id'] == second['result']['id']
    assert len(store.notes()) == 1
    rejected = action.run(dict(args, content='Changed finding')).payload
    assert not rejected['ok'] and 'different content' in rejected['error']
    draft = action.run(dict(operation='read_draft', draft_id=rejected['draft_id'])).payload
    assert draft['result']['content'] == 'Changed finding'
    repaired = action.run(dict(args, content='Changed finding', publication_id='revision',
                               relation='contradicts', references=first['result']['id'])).payload
    assert repaired['ok']
    note = store.read_note(repaired['result']['id'])
    assert note['provenance']['relation'] == 'contradicts'
    assert note['references'] == [first['result']['id']]
    long_title = action.run(dict(args, title='x' * 250, publication_id='long-title')).payload
    assert long_title['ok'] and long_title['result']['title'] == 'x' * 200
    assert long_title['result']['provenance']['submitted_title'] == 'x' * 250
