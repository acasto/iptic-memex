"""Tool exchange, input, cancellation, and history invariants without model calls."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event

import pytest

from app.chat import configure_chat, run_chat_turn
from app.completion import run_completion
from base_classes import Completed, InteractionNeeded, StepwiseAction
from core.cancellation import CancellationToken
from core.runtime import RunRequest, execute
from core.runtime.interaction import InteractionService


class ScriptedProvider:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.current = {}

    def chat(self):
        self.current = self.script[self.calls]
        self.calls += 1
        return self.current.get('text', '')

    def stream_chat(self):
        yield self.chat()

    def get_finish_reason(self):
        return self.current.get('reason', 'tool_calls' if self.current.get('tools') else 'stop')

    def get_tool_calls(self):
        return self.current.pop('tools', [])

    def get_assistant_metadata(self):
        return {'provider_native': {'keep': True}}

    def get_usage(self):
        return {'calls': self.calls}


def native(name='probe', call_id='call-1', args=None):
    return {'id': call_id, 'name': name, 'arguments': args or {}}


def session_with_tools(builder, script, behavior=None, tools='probe', **options):
    session = builder.build(mode='completion', active_tools_agent=tools, **options)
    session.provider = ScriptedProvider(script)
    seen = []

    class Probe(StepwiseAction):
        def __init__(self, session):
            self.session = session

        def start(self, args, content):
            seen.append(('start', args, content))
            return behavior(self, args, content) if behavior else Completed({'ok': True, 'answer': 42})

        def resume(self, state, response):
            seen.append(('resume', state, response))
            return Completed({'ok': True, 'response': response})

    session._registry._action_cache['probe_tool'] = Probe
    session.set_user_data('__dynamic_tools__', {'probe': {
        'function': {'type': 'action', 'name': 'probe_tool', 'fixed_args': {'pinned': 'safe'}},
        'args': ['pinned', 'block'], 'description': 'Offline probe', 'required': [],
    }})
    return session, seen


@pytest.mark.parametrize('stream', [False, True])
def test_official_tools_execute_once_and_follow_up_without_fake_user_turn(builder, stream):
    session, seen = session_with_tools(builder, [
        {'tools': [native(args={'pinned': 'unsafe'})]}, {'text': 'Answer with evidence'},
    ])
    events = []
    result = run_completion(session=session, message='Investigate', stream=stream, on_event=events.append)
    assert result.status == 'completed' and result.turns == 2 and result.tool_calls == 1
    assert result.last_text == 'Answer with evidence'
    assert len(seen) == 1 and seen[0][1]['pinned'] == 'safe'
    history = session.get_context('chat').get('all')
    assert [t['role'] for t in history] == ['user', 'assistant', 'tool', 'assistant']
    assert history[1]['tool_calls'][0]['id'] == history[2]['tool_call_id'] == 'call-1'
    assert history[1]['provider_native'] == {'keep': True}
    assert json.loads(history[2]['message'])['answer'] == 42
    assert not session.get_flag('auto_submit')
    assert len([e for e in events if e.type == 'tool_finished']) == 1


def test_pseudo_tools_share_executor_and_expand_labeled_content(builder):
    block = '%%BLOCK:note%%\nSaved content\n%%END%%\n'
    command = '%%probe%%\nblock="note"\n%%END%%'
    session, seen = session_with_tools(builder, [{'text': block + command}, {'text': 'Done'}])
    session.set_option('tool_mode', 'pseudo')
    result = run_completion(session=session, message='Investigate')
    assert result.status == 'completed' and result.tool_calls == 1
    assert len(seen) == 1 and seen[0][2].strip() == 'Saved content'
    assert [t['role'] for t in session.get_context('chat').get('all')] == [
        'user', 'assistant', 'assistant', 'assistant']


@pytest.mark.parametrize('mode', ['official', 'pseudo'])
@pytest.mark.parametrize('name', ['cmd', 'file'])
def test_tool_start_retains_bounded_metadata_and_executes_full_inputs(builder, mode, name):
    content = 'Full input content\n' * 2000
    args = {'desc': 'Describe the operation ' * 200, 'command': 'echo',
            'arguments': 'argument ' * 2000, 'unrelated': 'private input ' * 2000}
    if mode == 'official':
        script = [{'tools': [native(name, args=dict(args, content=content))]}]
    else:
        fields = '\n'.join(f'{key}="{value}"' for key, value in args.items())
        script = [{'text': (f'%%BLOCK:input%%\n{content}%%END%%\n'
                            f'%%{name}%%\n{fields}\nblock="input"\n%%END%%')}]
    session, seen = session_with_tools(builder, script)
    spec = session.get_user_data('__dynamic_tools__')['probe']
    spec['args'] = [*args, 'block']
    session.set_user_data('__dynamic_tools__', {name: spec})
    session.set_option('tool_mode', mode)
    session.write_policy = None
    result = execute(session, RunRequest(execute_tools=True, max_calls=1))

    assert result.tool_calls == 1 and result.tool_failures == 0
    assert len(seen) == 1
    for key, value in args.items():
        assert seen[0][1][key] == value
    assert seen[0][2].strip() == content.strip()
    started = next(event for event in result.events if event.type == 'tool_started')
    keys = ('desc', 'command', 'arguments') if name == 'cmd' else ('desc',)
    assert started.data['args'] == {key: args[key].strip()[:1024] for key in keys}
    if name == 'cmd':
        assert started.data['content'] == content.strip()[:1024]
    else:
        assert 'content' not in started.data
    assert len(json.dumps(started.data)) < 5000


def test_action_exception_is_not_retried_and_is_a_tool_failure(builder):
    def fail(*args):
        raise RuntimeError('Side effect failed')

    session, seen = session_with_tools(builder, [{'tools': [native()]}, {'text': 'Explains failure'}], fail)
    result = run_completion(session=session)
    assert result.status == 'completed' and result.tool_failures == 1
    assert len(seen) == 1
    assert any(e.type == 'tool_finished' and e.data['status'] == 'failed' for e in result.events)
    assert 'Side effect failed' in session.get_context('chat').get('all')[2]['message']


def test_completed_ok_false_is_not_reported_as_success(builder):
    session, _ = session_with_tools(builder, [{'tools': [native()]}, {'text': 'Reports failure'}],
                                    lambda *args: Completed({'ok': False, 'error': 'Rejected'}))
    result = run_completion(session=session)
    assert result.tool_failures == 1
    assert any(e.type == 'tool_finished' and e.data['status'] == 'failed' for e in result.events)


def test_stepwise_input_resumes_same_instance_once(builder):
    def needs(*args):
        raise InteractionNeeded('bool', {'prompt': 'Proceed?'}, 'next')

    session, seen = session_with_tools(builder, [{'tools': [native()]}, {'text': 'Done'}], needs)
    service = InteractionService(lambda request: True)
    result = run_completion(session=session, interactions=service)
    assert result.status == 'completed'
    assert [entry[0] for entry in seen] == ['start', 'resume']
    assert seen[1][1:] == ('next', True)


def test_waiting_input_accepts_one_response_by_id_and_rejects_stale_input(builder):
    def needs(action, args, content):
        value = action.session.ui.ask_choice('Choose', ['a', 'b'])
        return Completed({'ok': True, 'choice': value})

    session, seen = session_with_tools(builder, [{'tools': [native()]}, {'text': 'Done'}], needs)
    service = InteractionService(wait=True)
    asked = Event()
    requests = []

    def observe(event):
        if event.type == 'interaction_requested':
            requests.append(event.data)
            asked.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_completion, session=session, interactions=service, on_event=observe)
        try:
            assert asked.wait(5)
            request_id = requests[0]['id']
            with pytest.raises(ValueError, match='offered choices'):
                service.respond(request_id, 'invalid')
            service.respond(request_id, 'b')
            with pytest.raises(ValueError):
                service.respond(request_id, 'a')
            result = future.result(timeout=5)
        finally:
            if not future.done():
                session.get_cancellation_token().cancel('Test cleanup')
    assert result.status == 'completed'
    assert len(seen) == 1


def test_cancellation_wakes_input_and_completes_every_native_id(builder):
    def needs(action, args, content):
        action.session.ui.ask_bool('Proceed?')
        return Completed({'ok': True})

    session, seen = session_with_tools(builder, [{'tools': [native(call_id='one'), native(call_id='two')]}], needs)
    token, asked = CancellationToken(), Event()

    def observe(event):
        if event.type == 'interaction_requested':
            asked.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_completion, session=session, interactions=InteractionService(wait=True),
                             cancellation=token, on_event=observe)
        assert asked.wait(5)
        token.cancel('Stop')
        result = future.result(timeout=5)
    assert result.status == 'cancelled' and len(seen) == 1
    assert [t['tool_call_id'] for t in session.get_context('chat').get('all') if t['role'] == 'tool'] == ['one', 'two']


def test_unattended_missing_input_preserves_valid_history(builder):
    def needs(action, args, content):
        action.session.ui.ask_bool('Proceed?')

    session, seen = session_with_tools(builder, [{'tools': [native(call_id='one'), native(call_id='two')]}], needs)
    result = run_completion(session=session)
    assert result.status == 'needs_input' and len(seen) == 1
    assert [t['tool_call_id'] for t in session.get_context('chat').get('all') if t['role'] == 'tool'] == ['one', 'two']


def test_call_limit_stops_after_results_without_invalid_history(builder):
    session, seen = session_with_tools(builder, [{'tools': [native()]}])
    result = execute(session, RunRequest(execute_tools=True, max_calls=1))
    assert result.status == 'incomplete' and result.stop_reason == 'call_limit'
    assert result.turns == result.tool_calls == 1 and len(seen) == 1
    assert session.get_context('chat').get('all')[-1]['tool_call_id'] == 'call-1'


def test_truncated_calls_and_remaining_native_ids_are_answered_without_execution(builder):
    session, seen = session_with_tools(builder, [
        {'tools': [dict(native(call_id='one'), truncated=True), native(call_id='two')], 'reason': 'length'}])
    result = run_completion(session=session)
    assert result.status == 'incomplete' and result.truncated and not seen
    assert [t['tool_call_id'] for t in session.get_context('chat').get('all') if t['role'] == 'tool'] == ['one', 'two']


@pytest.mark.parametrize('allowed', [True, False])
def test_real_file_confirmation_uses_same_service_and_shows_diff(builder, tmp_path, allowed):
    path = tmp_path / 'note.txt'
    path.write_text('Original\n')
    session, _ = session_with_tools(builder, [
        {'tools': [native('file', args={'mode': 'write', 'file': 'note.txt', 'content': 'Changed\n'})]},
        {'text': 'Done'},
    ], tools='file', base_directory=str(tmp_path))
    session.write_policy = None
    prompts = []
    service = InteractionService(lambda request: prompts.append(request.spec['prompt']) or allowed)
    result = run_completion(session=session, interactions=service)
    assert result.status == 'completed'
    assert len(prompts) == 1 and '-Original' in prompts[0] and '+Changed' in prompts[0]
    assert path.read_text() == ('Changed\n' if allowed else 'Original\n')


def test_file_policy_and_sandbox_are_preserved(builder, tmp_path):
    session, _ = session_with_tools(builder, [
        {'tools': [native('file', args={'mode': 'write', 'file': 'denied.txt', 'content': 'No'})]},
        {'text': 'Writes blocked'},
    ], tools='file', base_directory=str(tmp_path))
    result = run_completion(session=session)
    assert result.tool_failures == 1 and not (tmp_path / 'denied.txt').exists()


def test_chat_and_completion_use_the_same_tool_lifecycle(builder):
    session, seen = session_with_tools(builder, [{'tools': [native()]}, {'text': 'Done'}])
    configure_chat(session)
    result = run_chat_turn(session, 'Investigate')
    assert result.status == 'completed' and result.turns == 2 and len(seen) == 1
    assert [t['role'] for t in session.get_context('chat').get('all')] == ['user', 'assistant', 'tool', 'assistant']


@pytest.mark.parametrize('args', [
    ['--tools', 'probe', '--message', 'Investigate'],
    ['--tools', 'probe', 'chat'],
])
def test_actual_cli_routes_tools_through_runtime(builder, monkeypatch, args):
    import main
    from click.testing import CliRunner
    from component_registry import ComponentRegistry
    from core.utils import UtilsHandler
    from types import SimpleNamespace
    from utils.input_utils import InputHandler

    session, seen = session_with_tools(builder, [{'tools': [native()]}, {'text': 'Final answer'}])
    registry = session._registry
    original = ComponentRegistry.get_action_class

    def action_class(self, name):
        if name == 'probe_tool':
            return registry._action_cache['probe_tool']
        return original(self, name)

    def provider_factory(live_session):
        live_session.set_user_data('__dynamic_tools__', session.get_user_data('__dynamic_tools__'))
        return session.provider

    cfg = builder.config_manager.base_config
    cfg['DEFAULT'].update({'user_label': 'You', 'response_label': 'Assistant',
                           'user_label_color': '', 'response_label_color': '', 'stream': 'false'})
    cfg['TOOLS'] = {'allow_auto_submit': 'true'}
    monkeypatch.setattr(main, 'ConfigManager', lambda conf: builder.config_manager)
    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda self, name: provider_factory)
    monkeypatch.setattr(ComponentRegistry, 'get_action_class', action_class)
    monkeypatch.setattr(UtilsHandler, 'tab_completion', property(lambda self: SimpleNamespace(
        run=lambda *a: None, set_session=lambda *a: None)))
    inputs = iter(['Investigate'])

    def get_input(*a, **kw):
        try:
            return next(inputs)
        except StopIteration:
            raise KeyboardInterrupt()

    monkeypatch.setattr(InputHandler, 'get_input', get_input)
    result = CliRunner().invoke(main.cli, args)
    assert 'Final answer' in result.stdout, result.output
    assert len(seen) == 1 and session.provider.calls == 2


def test_session_mutation_is_rejected_during_execution(builder):
    from core.session_persistence import apply_session_data
    session, _ = session_with_tools(builder, [{'text': 'Done'}])
    rejected = []

    def observe(event):
        if event.type == 'provider_started':
            for mutate in (lambda: session.switch_model('explicit-model'),
                           lambda: apply_session_data(session, {'id': 'other'})):
                with pytest.raises(RuntimeError, match='active run'):
                    mutate()
                rejected.append(True)
            command = session.get_action('chat_commands')
            assert command.run('/set model explicit-model') is True
            assert session.get_params()['model'] == 'worker-model'

    result = run_completion(session=session, on_event=observe)
    assert result.status == 'completed' and rejected == [True, True]
    assert session._active_run_id is None


def test_sandbox_read_escape_is_a_failure_not_a_successful_tool(builder, tmp_path):
    session, _ = session_with_tools(builder, [
        {'tools': [native('file', args={'mode': 'read', 'file': '../outside.txt'})]}, {'text': 'Blocked'},
    ], tools='file', base_directory=str(tmp_path))
    result = run_completion(session=session)
    assert result.status == 'completed' and result.tool_failures == 1


def test_file_creation_has_no_spurious_read_error(builder, tmp_path):
    session, _ = session_with_tools(builder, [
        {'tools': [native('file', args={'mode': 'write', 'file': 'new.txt', 'content': 'New\n'})]},
        {'text': 'Created'},
    ], tools='file', base_directory=str(tmp_path))
    session.write_policy = None
    result = run_completion(session=session, interactions=InteractionService(lambda request: True))
    assert result.status == 'completed' and result.tool_failures == 0
    assert (tmp_path / 'new.txt').read_text() == 'New\n'


def test_api_name_mapping_and_hard_blocklist_are_applied(builder):
    session, seen = session_with_tools(builder, [
        {'tools': [native('safe_api_name')]}, {'text': 'Mapped'},
    ])
    session.set_user_data('__tool_api_to_cmd__', {'safe_api_name': 'probe'})
    result = run_completion(session=session)
    assert result.status == 'completed' and len(seen) == 1
    session.config.base_config.set('AGENT', 'blocked_tools', 'probe')
    assert 'probe' not in session.get_action('assistant_commands').commands


def test_malformed_arguments_are_results_not_empty_argument_execution(builder):
    session, seen = session_with_tools(builder, [
        {'tools': [dict(native(), arguments='not valid arguments')]}, {'text': 'Rejected'},
    ])
    result = run_completion(session=session)
    assert not seen and result.tool_failures == 1 and result.turns == 2


def test_completion_follow_up_does_not_repeat_tool_contexts(builder, tmp_path):
    path = tmp_path / 'evidence.txt'
    path.write_text('Unique evidence')
    session, _ = session_with_tools(builder, [
        {'tools': [native('file', args={'mode': 'read', 'file': 'evidence.txt'})]}, {'text': 'Answer'},
    ], tools='file', base_directory=str(tmp_path))
    result = run_completion(session=session)
    history = session.get_context('chat').get('all')
    assert result.status == 'completed'
    assert 'Unique evidence' in history[2]['message']
    assert not history[-1].get('context') and session.get_context('file') is None


def test_chat_hooks_are_application_services_not_observer_side_effects(builder, monkeypatch):
    phases = []
    session, _ = session_with_tools(builder, [{'text': 'Answer'}])
    configure_chat(session)
    monkeypatch.setattr('core.hooks.run_hooks', lambda active, phase, extras=None: phases.append(phase))
    result = run_chat_turn(session, 'Question', on_event=lambda event: (_ for _ in ()).throw(RuntimeError('Observer failed')))
    assert result.status == 'completed'
    assert phases == ['session_start', 'pre_turn', 'post_turn']


def test_application_finalization_failure_restores_configuration_and_releases_session(builder):
    session = builder.build(mode='completion')
    before = dict(session.config.overrides)

    def fail(*args):
        raise RuntimeError('Finalization failed')

    result = execute(session, RunRequest(), after_run=fail)
    assert result.status == 'failed' and result.stop_reason == 'finalization_error'
    assert session.config.overrides == before
    assert execute(session, RunRequest()).status == 'completed'


@pytest.mark.parametrize('policy', ['DENY', 'invalid-policy'])
def test_noninteractive_write_policy_cannot_fall_through_to_allow(builder, tmp_path, policy):
    builder.config_manager.base_config.set('AGENT', 'writes_policy', policy)
    session, _ = session_with_tools(builder, [
        {'tools': [native('file', args={'mode': 'write', 'file': 'blocked.txt', 'content': 'No'})]},
        {'text': 'Blocked'},
    ], tools='file', base_directory=str(tmp_path))
    result = run_completion(session=session)
    assert result.tool_failures == 1 and not (tmp_path / 'blocked.txt').exists()


def test_unattended_file_confirmation_is_emitted_once(builder, tmp_path):
    session, _ = session_with_tools(builder, [
        {'tools': [native('file', args={'mode': 'write', 'file': 'pending.txt', 'content': 'Pending'})]},
    ], tools='file', base_directory=str(tmp_path))
    session.write_policy = None
    result = run_completion(session=session)
    assert result.status == 'needs_input'
    assert len([e for e in result.events if e.type == 'interaction_requested']) == 1
    assert not (tmp_path / 'pending.txt').exists()


def test_tool_images_are_delivered_as_media_without_binary_in_result_text(builder):
    from core.provider_turns import provider_turns
    from types import SimpleNamespace
    image = {'type': 'image', 'context': SimpleNamespace(
        get=lambda: {'mime_type': 'image/png', 'content': 'sensitive-base64-image'})}

    def creates_image(action, args, content):
        action.session.context.setdefault('image', []).append(image['context'])
        return Completed({'ok': True})

    session, _ = session_with_tools(builder, [
        {'tools': [native(call_id='one'), native(call_id='two')]}, {'text': 'Answer'},
    ], creates_image)
    result = run_completion(session=session)
    assert result.status == 'completed'
    history = session.get_context('chat').get('all')
    assert [t['role'] for t in history] == ['user', 'assistant', 'tool', 'tool', 'assistant']
    assert all('sensitive-base64-image' not in t['message'] for t in history)
    wire = list(provider_turns(history))
    assert [t['role'] for t in wire] == ['user', 'assistant', 'tool', 'tool', 'user', 'assistant']
    assert len(wire[4]['context']) == 2 and wire[4]['meta']['kind'] == 'tool_media'


def test_large_tool_result_is_gated_before_the_next_provider_call(builder, tmp_path):
    (tmp_path / 'large.txt').write_text('Evidence ' * 2000)
    builder.config_manager.base_config.set('AGENT', 'large_input_limit', '100')
    session, _ = session_with_tools(builder, [
        {'tools': [native('file', args={'mode': 'read', 'file': 'large.txt'})]},
    ], tools='file', base_directory=str(tmp_path))
    result = run_completion(session=session, message='Read')
    assert result.stop_reason == 'large_input_limit_exceeded' and result.turns == 1
    assert session.provider.calls == 1
    assert session.get_context('chat').get('all')[-1]['tool_call_id'] == 'call-1'
