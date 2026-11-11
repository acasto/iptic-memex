"""Offline execution contracts using real session/config/context assembly."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.completion import run_completion
from core.cancellation import CancellationToken
from core.runtime import RunRequest, execute


def test_completion_assembly_uses_agent_defaults_and_has_no_ui(builder, capsys):
    session = builder.build(mode='completion')
    assert session.get_params()['model'] == 'worker-model'
    assert session.ui is None
    assert session.get_effective_tool_mode() == 'none'
    assert session.get_user_data('__mcp_client__') is None
    assert not session.get_flag('completion_mode')
    explicit = builder.build(mode='completion', model='explicit-model')
    assert explicit.get_params()['model'] == 'explicit-model'
    result = run_completion(session=session, message='Explain')
    assert result.status == 'completed'
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('stream', [False, True])
def test_same_lifecycle_for_streamed_and_collected_execution(builder, capsys, stream):
    session = builder.build(mode='completion')
    observed = []
    result = run_completion(session=session, message='Explain', stream=stream, on_event=observed.append)
    assert result.status == 'completed'
    assert result.last_text == 'A useful answer'
    assert result.turns == 1 and session.provider.calls == 1
    assert observed[0].type == 'run_started'
    assert observed[-1].type == 'run_finished'
    assert [e.sequence for e in observed] == list(range(1, len(observed) + 1))
    assert {e.run_id for e in observed} == {result.run_id}
    assert all(e.session_id == session.session_uid for e in observed)
    assert len([e for e in observed if e.type == 'assistant_message']) == 1
    assert len([e for e in observed if e.type == 'usage']) == 1
    assert not any(e.type == 'assistant_delta' for e in result.events)
    if stream:
        assert ''.join(e.data['text'] for e in observed if e.type == 'assistant_delta') == result.last_text
        assert session.provider.closed
    turns = session.get_context('chat').get('all')
    assert [t['role'] for t in turns] == ['user', 'assistant']
    assert turns[-1]['native_continuation'] == {'opaque': 'provider-owned'}
    assert capsys.readouterr().out == ''


def test_file_and_stdin_contexts_keep_task_separate(builder, tmp_path):
    source = tmp_path / 'source.txt'
    source.write_text('Supporting evidence')
    session = builder.build(mode='completion')
    result = run_completion(session=session, contexts=[('file', str(source)),
                             ('file', {'name': 'stdin', 'content': 'Task from stdin'})],
                            stdin_as_message=True)
    assert result.status == 'completed'
    turn = session.get_context('chat').get('all')[0]
    assert turn['message'] == 'Task from stdin'
    assert [c['context'].get()['content'] for c in turn['context']] == ['Supporting evidence']
    assert session.get_context('file') is None


def test_input_limit_includes_system_prompt_and_makes_no_call(builder):
    session = builder.build(mode='completion', prompt='A long explicit system prompt for the task')
    result = execute(session, RunRequest(message='Hi', input_limit=1))
    assert result.status == 'failed'
    assert result.stop_reason == 'large_input_limit_exceeded'
    assert session.provider.calls == result.turns == 0


@pytest.mark.parametrize('stream', [False, True])
def test_filters_work_across_chunk_boundaries_and_preserve_parser_text(builder, stream):
    session = builder.build(mode='completion', output_filters='tool_call')
    session.provider.text = 'Before\n%%TEST%%\nsecret\n%%END%%\nAfter'
    session.provider.chunks = ['Before\n%', '%TEST%%\nsecret\n%%EN', 'D%%\nAfter']
    result = run_completion(session=session, message='Explain', stream=stream)
    assert result.status == 'completed'
    assert 'secret' not in result.last_text
    assert 'Before' in result.last_text and 'After' in result.last_text
    assert result.sanitized == session.provider.text


@pytest.mark.parametrize('reason,status', [('length', 'incomplete'), ('error', 'failed'),
                                        ('cancelled', 'cancelled')])
def test_provider_outcomes_are_not_inferred_from_prose(builder, reason, status):
    session = builder.build(mode='completion')
    session.provider.reason = reason
    result = run_completion(session=session, message='Explain')
    assert result.status == status
    assert result.last_text == 'A useful answer'
    assert result.truncated == (reason == 'length')


def test_partial_stream_failure_is_saved_and_stream_is_closed(builder):
    session = builder.build(mode='completion')
    session.provider.failure = RuntimeError('Stream failed')
    observed = []
    result = run_completion(session=session, stream=True, on_event=observed.append)
    assert result.status == 'failed' and result.error == 'Stream failed'
    assert result.last_text == 'A useful answer'
    assert session.get_context('chat').get('all')[-1]['message'] == 'A useful answer'
    assert session.provider.closed and observed[-1].type == 'run_finished'


def test_cancel_before_start_makes_no_call(builder):
    session = builder.build(mode='completion')
    token = CancellationToken()
    token.cancel('Cancelled before submission')
    result = run_completion(session=session, cancellation=token)
    assert result.status == 'cancelled'
    assert session.provider.calls == result.turns == 0


def test_cancel_stream_keeps_partial_answer_and_prevents_more_tokens(builder):
    session = builder.build(mode='completion')
    token = CancellationToken()

    def observe(event):
        if event.type == 'assistant_delta':
            token.cancel('Enough')

    result = run_completion(session=session, stream=True, on_event=observe, cancellation=token)
    assert result.status == 'cancelled' and result.last_text == 'A useful '
    assert session.provider.closed


def test_run_lock_rejects_overlap_but_other_sessions_can_run(builder):
    session = builder.build(mode='completion')
    entered, release = Event(), Event()

    def block():
        entered.set()
        assert release.wait(5)
        return 'Answer'

    session.provider.chat = block
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_completion, session=session)
        try:
            assert entered.wait(5)
            with pytest.raises(RuntimeError, match='active run'):
                run_completion(session=session, contexts=[('file', {'name': 'leak', 'content': 'No'})])
            assert session.get_context('file') is None
            independent = run_completion(session=builder.build(mode='completion'))
            assert independent.status == 'completed'
        finally:
            release.set()
        assert future.result(timeout=5).status == 'completed'


def test_observer_failure_is_visible_and_does_not_lose_answer(builder):
    def broken(event):
        raise RuntimeError('Observer unavailable')

    result = run_completion(session=builder.build(mode='completion'), on_event=broken)
    assert result.status == 'completed' and result.last_text == 'A useful answer'
    assert any(e.data.get('code') == 'observer_failed' for e in result.events)


def test_raw_capture_is_opt_in_and_not_in_events(builder):
    session = builder.build(mode='completion')
    result = run_completion(session=session, capture='raw')
    assert result.raw == {'native': 'opaque provider payload'}
    assert all('opaque provider payload' not in str(e.data) for e in result.events)
    assert run_completion(session=builder.build(mode='completion')).raw is None


def test_request_stream_setting_does_not_change_resolved_configuration(builder):
    session = builder.build(mode='completion')
    before = dict(session.config.overrides)
    result = run_completion(session=session, stream=False)
    assert result.status == 'completed'
    assert session.config.overrides == before
    assert session.get_params()['stream'] is True


def test_model_addenda_apply_without_an_interactive_prompt(builder):
    builder.config_manager.models.set('worker-model', 'supplemental_prompt', 'Model guidance')
    session = builder.build(mode='completion')
    assert 'Model guidance' in session.get_context('prompt').get()['content']


def test_missing_usage_is_unknown_and_does_not_discard_answer(builder):
    session = builder.build(mode='completion')

    def missing():
        raise RuntimeError('Usage unavailable')

    session.provider.get_usage = missing
    result = run_completion(session=session)
    assert result.status == 'completed' and result.last_text == 'A useful answer'
    assert result.usage is None
    assert any(e.data.get('code') == 'metric_unavailable' for e in result.events)


def test_unresolved_input_is_explicit_and_not_automatically_replayed(builder):
    from base_classes import InteractionNeeded
    session = builder.build(mode='completion')

    def needs_input():
        session.provider.calls += 1
        raise InteractionNeeded('bool', {'prompt': 'Continue?'}, 'not-durable')

    session.provider.chat = needs_input
    result = run_completion(session=session)
    assert result.status == 'needs_input' and session.provider.calls == 1
    assert any(e.type == 'interaction_requested' for e in result.events)


def test_optional_services_are_not_started_by_completion(builder, monkeypatch, tmp_path):
    def unwanted(*args, **kwargs):
        pytest.fail('Completion implicitly started an optional service')

    monkeypatch.setattr('core.memory_files.prepare_memory', unwanted)
    monkeypatch.setattr('core.hooks.run_hooks', unwanted)
    monkeypatch.setattr('memex_mcp.bootstrap.autoload_mcp', unwanted)
    builder.config_manager.base_config['MEMORY'] = {
        'active': 'true', 'directory': str(tmp_path / 'memory')}
    result = run_completion(session=builder.build(mode='completion'), message='Explain')
    assert result.status == 'completed'
    assert not (tmp_path / 'memory').exists()


def test_headless_completion_never_imports_a_frontend_or_old_runner(builder, monkeypatch):
    import builtins
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == 'ui' or name.startswith('ui.') or name in ('core.turns', 'core.null_ui', 'legacy.turns', 'legacy.null_ui'):
            raise AssertionError(f'Completion imported legacy execution/frontend: {name}')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded)
    result = run_completion(session=builder.build(mode='completion'), message='Explain')
    assert result.status == 'completed'


@pytest.mark.parametrize('args,stdin,expected', [
    (['--message', 'Explain'], None, 'A useful answer\n'),
    (['-s', '--message', 'Explain'], None, 'A useful answer\n'),
    (['-f', '-'], 'Explain from stdin', 'A useful answer\n'),
    (['--raw', '--message', 'Explain'], None, '{"native": "opaque provider payload"}\n'),
])
def test_cli_uses_real_assembly_and_runtime(builder, monkeypatch, args, stdin, expected):
    import main
    from click.testing import CliRunner
    monkeypatch.setattr(main, 'ConfigManager', lambda conf: builder.config_manager)
    result = CliRunner().invoke(main.cli, args, input=stdin)
    assert result.exit_code == 0, result.output
    assert result.stdout == expected


def test_cancellation_cleanup_can_register_cleanup_and_cancel_again():
    token = CancellationToken()
    calls = []

    def cleanup():
        calls.append('first')
        token.register_cleanup(lambda: calls.append('late'))
        token.cancel('again')

    token.register_cleanup(cleanup)
    # A regression would deadlock inside cleanup; don't hang the pytest process.
    from threading import Thread
    thread = Thread(target=token.cancel, daemon=True)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    token.cancel()
    assert calls == ['first', 'late']


@pytest.mark.parametrize('window,expected_calls', [('all', 0), ('last_1', 1)])
def test_hard_input_limit_counts_pending_provider_window(builder, window, expected_calls):
    session = builder.build(mode='completion', context_sent=window)
    session.add_context('chat').add('old history ' * 200, 'user')
    result = execute(session, RunRequest(message='Hi', input_limit=100))
    assert session.provider.calls == expected_calls
    assert result.status == ('completed' if expected_calls else 'failed')
    session.close()
