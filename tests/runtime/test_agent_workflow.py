"""Agent policy on the real shared runtime, without backend requests."""

import builtins
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from app.agent import run_agent
from app.runs import run_completion
from core.cancellation import CancellationToken
from core.runtime import RunOutcome


def scripted(session, responses):
    provider = session.provider
    current = {}

    def chat():
        current.clear()
        current.update(responses[provider.calls])
        provider.calls += 1
        return current.get('text', '')

    provider.chat = chat
    provider.get_finish_reason = lambda: current.get('reason', 'tool_calls' if current.get('calls') else 'stop')
    provider.get_tool_calls = lambda: current.pop('calls', [])
    return provider


def file_call():
    return {'id': 'read-1', 'name': 'file', 'arguments': {'mode': 'read', 'file': 'evidence.txt'}}


@pytest.mark.parametrize('model', [None, 'explicit-model'])
def test_agent_headless_assembly_resolves_models_without_a_ui(builder, model, capsys):
    options = {'stream': False}
    if model:
        options['model'] = model
    session = builder.build(mode='internal', **options)
    session.provider.text = 'Answer %%DONE%%'
    result = run_agent(session=session, steps=3)
    assert isinstance(result, RunOutcome) and result.status == 'completed'
    assert result.last_text == 'Answer' and result.stop_reason == 'sentinel'
    assert session.get_params()['model'] == (model or 'worker-model')
    assert session.ui is None and not session.in_agent_mode()
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('steps', [1, 3])
def test_tools_use_one_budget_and_leave_valid_history(builder, tmp_path, steps):
    (tmp_path / 'evidence.txt').write_text('Source evidence')
    session = builder.build(mode='agent', stream=False, active_tools_agent='file', base_directory=str(tmp_path))
    provider = scripted(session, [{'calls': [file_call()]}, {'text': 'Grounded answer %%DONE%%'}])
    result = run_agent(session=session, steps=steps)
    assert result.turns == provider.calls == min(steps, 2)
    assert result.tool_calls == 1
    history = session.get_context('chat').get('all')
    assert sum(turn['role'] == 'user' for turn in history) == 1
    tool = next(turn for turn in history if turn['role'] == 'tool')
    assert tool['tool_call_id'] == 'read-1' and 'Source evidence' in tool['message']
    assert result.status == ('incomplete' if steps == 1 else 'completed')
    assert result.stop_reason == ('steps' if steps == 1 else 'sentinel')


def test_call_updates_reach_provider_and_observers_cannot_supply_required_state(builder, tmp_path):
    (tmp_path / 'evidence.txt').write_text('Evidence')
    session = builder.build(mode='agent', stream=False, active_tools_agent='file', base_directory=str(tmp_path))
    scripted(session, [{'calls': [file_call()]}, {'text': 'Answer'}])
    original = session.provider.chat
    seen = []

    def chat():
        view = session.get_context('chat').get()
        data = [ctx['context'].get()['content'] for turn in view for ctx in turn.get('context') or []]
        seen.append(data)
        return original()

    session.provider.chat = chat

    def before(active, step):
        active.add_context('agent', {'name': 'peer_update', 'content': f'Peer finding {step}'})

    def broken_observer(event):
        raise RuntimeError('Optional observer unavailable')

    result = run_agent(session=session, steps=3, before_call=before, on_event=broken_observer)
    assert result.status == 'completed' and len(seen) == 2
    assert any('Peer finding 1' in content for content in seen[0])
    assert any('Peer finding 2' in content for content in seen[1])
    assert any(turn['role'] == 'context' for turn in session.get_context('chat').get('all'))


def test_status_tags_can_be_disabled_and_prompts_do_not_accumulate(builder):
    session = builder.build(mode='agent', stream=False)
    for _ in range(2):
        result = run_agent(session=session, steps=3, status_tags=False)
        assert result.status == 'completed' and result.turns == 1
    data = [ctx['context'].get()['content'] for turn in session.get_context('chat').get('all')
            for ctx in turn.get('context') or []]
    assert not any('<status>' in content for content in data)
    assert session.get_context('prompt').get()['content'].count('Finish signal:') == 1


def test_internal_agent_copies_contexts_but_uses_chat_only_as_template_seed(builder):
    outer = builder.build(mode='agent', stream=False)
    chat = outer.add_context('chat')
    chat.add('Parent question', 'user')
    chat.add('Parent answer', 'assistant')
    source = outer.add_context('file', {'name': 'evidence', 'content': 'Original',
                                       'metadata': {'nested': {'value': 'parent'}}})
    child = builder.build(mode='internal', stream=False)
    child.provider.text = 'Child answer'
    result = run_agent(session=child, steps=2, message='Child task', outer_session=outer)
    assert result.status == 'completed'
    assert child.get_user_data('__chat_seed__')[0]['message'] == 'Parent question'
    assert [t['message'] for t in child.get_context('chat').get('all') if t['role'] == 'user'] == ['Child task']
    copied = child.get_context('chat').get('all')[0]['context'][0]['context'].get()
    copied['content'] = 'Child edit'
    copied['metadata']['nested']['value'] = 'child'
    assert source.get()['content'] == 'Original'
    assert source.get()['metadata']['nested']['value'] == 'parent'
    assert child.session_uid != outer.session_uid
    assert child.utils.logger.get_context()['session_uid'] == child.session_uid


@pytest.mark.parametrize('agent', [False, True])
def test_cancelled_parent_prevents_starting_an_internal_run(builder, monkeypatch, agent):
    outer = builder.build(mode='agent', stream=False)
    token = CancellationToken()
    token.cancel('Parent stopped')
    outer.set_user_data('__turn_cancel__', token)

    def unwanted(*args, **kwargs):
        pytest.fail('Cancelled helper constructed another session')

    monkeypatch.setattr(builder, 'build', unwanted)
    fn = run_agent if agent else run_completion
    kwargs = {'steps': 3} if agent else {'message': 'Task'}
    result = fn(builder=builder, outer_session=outer, **kwargs)
    assert result.status == 'cancelled' and result.turns == 0


def test_parent_cancellation_reaches_a_running_child(builder):
    outer = builder.build(mode='agent', stream=False)
    parent = CancellationToken()
    outer.set_user_data('__turn_cancel__', parent)
    child = builder.build(mode='internal', stream=True)
    started, release = Event(), Event()

    def stream():
        started.set()
        assert release.wait(5)
        yield 'Late response'

    child.provider.stream_chat = stream
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_agent, session=child, steps=2, outer_session=outer)
        assert started.wait(5)
        parent.cancel('Parent stopped')
        release.set()
        result = future.result(timeout=5)
    assert result.status == 'cancelled' and not result.last_text


def test_agent_path_never_imports_legacy_runner_or_ui(builder, monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(('ui.', 'legacy.')) or name in ('core.turns', 'core.null_ui'):
            raise AssertionError(f'Unexpected dependency: {name}')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    result = run_agent(builder=builder, steps=2, overrides={'stream': False})
    assert result.status == 'completed'


def test_agent_presentation_does_not_select_provider_streaming(builder):
    session = builder.build(mode='agent', stream=True)
    events = []
    result = run_agent(session=session, steps=3, output='none', on_event=events.append)
    assert result.status == 'completed' and session.provider.closed
    assert any(event.type == 'assistant_delta' for event in events)


@pytest.mark.parametrize('agent', [False, True])
@pytest.mark.parametrize('failed', [False, True])
def test_owned_helpers_close_resources_and_inherit_only_the_sandbox(builder, agent, failed):
    outer = builder.build(mode='agent', model='explicit-model', base_directory='/tmp', stream=False)
    child = builder.build(mode='internal' if agent else 'completion', stream=False)
    child.provider.failure = RuntimeError('Backend failed') if failed else None
    cleaned = []
    child.provider.cleanup = lambda: cleaned.append('provider')
    child.register_cleanup_callback(lambda: cleaned.append('callback'))

    def build(**options):
        assert options == {'mode': 'internal' if agent else 'completion', 'base_directory': '/tmp'}
        return child

    fn = run_agent if agent else run_completion
    result = fn(builder=SimpleNamespace(build=build), outer_session=outer,
                **({'steps': 2} if agent else {'message': 'Task'}))
    assert result.status == ('failed' if failed else 'completed')
    assert cleaned == ['provider', 'callback']
    child.close()
    assert cleaned == ['provider', 'callback']
    assert not getattr(outer, '_closed', False)


def test_failed_assembly_releases_initialized_provider(builder, monkeypatch):
    from session import Session
    from component_registry import ComponentRegistry

    cleaned = []
    provider_class = ComponentRegistry.load_provider_class(None, 'Fake')
    monkeypatch.setattr(provider_class, 'cleanup', lambda self: cleaned.append(True), raising=False)
    original = Session.add_context
    monkeypatch.setattr(Session, 'add_context', lambda self, kind, data=None:
                        None if kind == 'prompt' else original(self, kind, data))
    with pytest.raises(ValueError, match='prompt context'):
        builder.build(mode='internal')
    assert cleaned == [True]


def test_seed_reaches_system_templates_and_policy_changes_replace_old_guidance(builder):
    outer = builder.build(mode='agent', stream=False)
    outer.add_context('chat').add('Parent evidence', 'user')
    child = builder.build(mode='internal', prompt='Review {{chat:last}}',
                          template_handler='prompt_template_chat', stream=False)
    result = run_agent(session=child, outer_session=outer, steps=1)
    assert result.status == 'completed'
    assert 'Parent evidence' in child.get_context('prompt').get()['content']
    run_agent(session=child, steps=1, writes_policy='allow')
    prompt = child.get_context('prompt').get()['content']
    assert 'Parent evidence' in prompt
    assert 'File writes are disabled' not in prompt
    assert prompt.count('Finish signal:') == 1
    assert not getattr(child, '_closed', False)


@pytest.mark.parametrize('enabled', [False, True])
def test_agent_mcp_is_explicit_and_completion_cannot_pass_it_through(builder, monkeypatch, enabled):
    from memex_mcp.policy import mcp_enabled
    import memex_mcp.client as clients
    import memex_mcp.bootstrap as bootstrap

    builder.config_manager.base_config.read_dict({
        'MCP': {'active': 'true', 'autoload': 'selected,other'},
        'MCP.selected': {'transport': 'provider', 'url': 'https://selected.invalid'},
        'MCP.other': {'transport': 'provider', 'url': 'https://other.invalid'},
        'AGENT': {'use_mcp': str(enabled), 'available_mcp': 'selected'},
    })
    connections, targets = [], []
    monkeypatch.setattr(clients, 'get_or_create_client', lambda session: connections.append(session))
    monkeypatch.setattr(bootstrap, '_apply_provider_passthrough',
                        lambda session, servers: targets.append(set(servers)))
    session = builder.build(mode='agent', stream=False)
    session.provider.supports_mcp_passthrough = lambda: True
    assert run_agent(session=session, steps=1).status == 'completed'
    assert mcp_enabled(session) == enabled
    assert targets == ([{'selected'}] if enabled else [])
    assert len(connections) == int(enabled)
    completion = builder.build(mode='completion')
    assert not mcp_enabled(completion)


@pytest.mark.parametrize('synthesis', [False, True])
def test_workspace_worker_uses_real_runtime_and_publication(builder, tmp_path, monkeypatch, synthesis):
    import json
    import core.workspace_worker as worker
    from core.workspace_state import WorkspaceStore

    store = WorkspaceStore(tmp_path / 'workspace')
    run = store.create_run('Question', {})
    children = []
    original = builder.build

    def build(**options):
        child = original(**options)
        children.append(child)
        if not synthesis:
            scripted(child, [{'calls': [{
                'id': 'publication', 'name': 'workspace', 'arguments': {
                    'operation': 'publish', 'title': 'Evidence', 'content': 'A sourced finding',
                }}]}, {'text': 'Grounded answer %%DONE%%'}])
        return child

    monkeypatch.setattr(worker, 'ConfigManager', lambda config: builder.config_manager)
    monkeypatch.setattr(worker, 'SessionBuilder', lambda config: SimpleNamespace(build=build))
    result = worker.execute(dict(
        directory=str(store.root), repository=str(tmp_path), run_id=run['id'],
        worker='worker-1', model='worker-model', prompt='Test prompt', message='Question',
        question='Question', steps=3, writes_policy='deny',
        overrides={'active_tools_agent': 'workspace'},
        phase='synthesis' if synthesis else 'worker',
    ))
    assert result['status'] == 'completed', result
    assert children[0]._closed and children[0].ui is None
    if synthesis:
        assert result['turns'] == 1 and not store.notes()
    else:
        assert result['turns'] == 2 and result['tool_calls'] == 1
        assert any(n['content'].strip() == 'A sourced finding' for n in store.notes())
        events_path = store.run_dir(run['id']) / 'worker-1' / 'events.jsonl'
        events = [json.loads(line)['event'] for line in events_path.read_text().splitlines()]
        assert 'step_start' in events and 'tool_end' in events and 'model_done' in events
