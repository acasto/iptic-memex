"""Exercise the CLI boundary with the real modes and shared runners, offline."""
import configparser
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import main
from contexts.chat_context import ChatContext
from contexts.file_context import FileContext
from utils.output_utils import OutputHandler
from utils.stream_utils import StreamHandler
from actions.assistant_output_action import AssistantOutputAction


class Session:
    def __init__(self, options, behavior):
        self.config = SimpleNamespace(overrides=dict(options))
        self.params = dict(options)
        self.context = {}
        self.flags = {}
        self.user_data = {}
        self.policy = None
        self.utils = SimpleNamespace(
            output=OutputHandler(self),
            fs=SimpleNamespace(resolve_file_path=lambda f: str(Path(f)) if Path(f).is_file() else None),
        )
        self.utils.stream = StreamHandler(self, self.utils.output)
        self.provider = Provider(self, behavior)

    def get_option(self, section, key, fallback=None):
        return {'colors': False, 'status_tags': False, 'active': False}.get(key, fallback)

    def get_params(self):
        return dict(self.params)

    def set_option(self, key, value):
        self.params[key] = value

    def get_flag(self, key):
        return self.flags.get(key, False)

    def set_flag(self, key, value):
        self.flags[key] = value

    def get_user_data(self, key, default=None):
        return self.user_data.get(key, default)

    def set_user_data(self, key, value):
        self.user_data[key] = value

    def get_context(self, kind):
        return next(iter(self.context.get(kind, [])), None)

    def add_context(self, kind, value=None):
        if kind == 'chat':
            ctx = ChatContext(self)
        elif kind == 'file':
            ctx = FileContext(self, value)
        else:
            data = value if isinstance(value, dict) else {'name': kind, 'content': value or ''}
            ctx = SimpleNamespace(get=lambda: data)
        self.context.setdefault(kind, []).append(ctx)
        return ctx

    def remove_context_type(self, kind):
        self.context.pop(kind, None)

    def get_action(self, name):
        if name == 'assistant_output':
            return AssistantOutputAction(self)
        if name == 'process_contexts':
            return SimpleNamespace(get_contexts=self.contexts,
                                   process_contexts_for_user=lambda **kw: self.contexts(self))
        return None

    def contexts(self, session):
        return [{'type': k, 'context': ctx} for k, entries in self.context.items()
                if k not in ('prompt', 'chat') for ctx in entries]

    def get_provider(self):
        return self.provider

    def enter_agent_mode(self, policy):
        self.policy = policy

    def exit_agent_mode(self):
        self.policy = None

    def in_agent_mode(self):
        return self.policy is not None

    def get_agent_write_policy(self):
        return self.policy


class Provider:
    def __init__(self, session, behavior):
        self.session = session
        self.behavior = behavior
        self.calls = 0
        self.reason = None

    def chat(self):
        self.calls += 1
        self.reason = self.behavior.get('reason', 'stop')
        if self.behavior.get('interrupt'):
            raise KeyboardInterrupt()
        if self.behavior.get('empty_exception'):
            raise RuntimeError()
        if self.behavior.get('exception'):
            raise RuntimeError('backend unavailable')
        if self.behavior.get('cancel'):
            self.session.set_flag('turn_cancelled', True)
        self.session.utils.output.warning('provider diagnostic')
        return self.behavior.get('text', 'Answer')

    def stream_chat(self):
        self.calls += 1
        yield 'Answer'
        if self.behavior.get('exception'):
            raise RuntimeError('stream interrupted')
        self.reason = self.behavior.get('reason', 'stop')

    def get_finish_reason(self):
        return self.reason

    def get_full_response(self):
        return {'choices': ['Answer']}

    def get_messages(self):
        return self.session.get_context('chat').get('all')

    def get_usage(self):
        return {'prompt_tokens': 10, 'completion_tokens': 2}


@pytest.fixture
def harness(monkeypatch):
    sessions = []
    behavior = {}

    class Config:
        def __init__(self, conf=None):
            self.base_config = configparser.ConfigParser()
            self.base_config['AGENT'] = {'default_steps': '1', 'writes_policy': 'deny'}

    class Builder:
        def __init__(self, config_manager):
            self.config_manager = config_manager

        def build(self, mode=None, **options):
            sess = Session(options, behavior)
            sessions.append(sess)
            return sess

    monkeypatch.setattr(main, 'ConfigManager', Config)
    monkeypatch.setattr(main, 'SessionBuilder', Builder)
    monkeypatch.setattr('core.turns.run_hooks', lambda *a, **kw: None)
    monkeypatch.setattr('memex_mcp.bootstrap.autoload_mcp', lambda *a, **kw: None)
    return CliRunner(), sessions, behavior


def invoke_json(harness, args=(), stdin=None):
    runner, sessions, behavior = harness
    result = runner.invoke(main.cli, [*args, 'agent', '--json'], input=stdin)
    return result, json.loads(result.stdout)


@pytest.mark.parametrize('behavior,status,reason', [
    ({'exception': True}, 'failed', 'error'),
    ({'empty_exception': True}, 'failed', 'error'),
    ({'interrupt': True}, 'cancelled', 'cancelled'),
    ({'reason': 'error', 'text': 'backend unavailable'}, 'failed', 'error'),
    ({'reason': 'content_filter'}, 'failed', 'content_filter'),
    ({'reason': 'length', 'text': 'Partial'}, 'incomplete', 'length'),
    ({'reason': 'length', 'text': 'Partial %%DONE%%'}, 'incomplete', 'length'),
    ({'cancel': True}, 'cancelled', 'cancelled'),
])
def test_json_unsuccessful_run_is_one_object_and_nonzero(harness, behavior, status, reason):
    harness[2].update(behavior)
    result, data = invoke_json(harness, ['--steps', '3'])
    assert result.exit_code == 1
    assert data['status'] == status
    assert data['stop_reason'] == reason
    assert data['error']
    assert harness[1][0].provider.calls == 1


@pytest.mark.parametrize('text', ['', 'Error: this is quoted source material'])
def test_json_empty_or_error_like_text_is_still_success(harness, text):
    harness[2]['text'] = text
    result, data = invoke_json(harness)
    assert result.exit_code == 0
    assert data['status'] == 'completed'
    assert data['error'] is None
    assert data['usage']['prompt_tokens'] == 10


def test_verbose_json_keeps_diagnostics_on_stderr(harness):
    result, data = invoke_json(harness, ['-v'])
    assert result.exit_code == 0
    assert data['last_text'] == 'Answer'
    assert 'provider diagnostic' in result.stderr
    assert 'BEFORE TURN 1' in result.stderr


@pytest.mark.parametrize('json_output', [False, True])
def test_root_and_command_files_execute_once_and_reach_provider(harness, tmp_path, json_output):
    first, second = tmp_path / 'first.txt', tmp_path / 'second.txt'
    first.write_text('First attachment')
    second.write_text('Second attachment')
    args = ['-f', str(first), '--steps', '3', 'agent', '-f', str(second), '--message', 'Summarize']
    if json_output:
        args += ['--json']
    result = harness[0].invoke(main.cli, args)
    assert result.exit_code == 0, result.output
    assert len(harness[1]) == 1
    sess = harness[1][0]
    assert sess.provider.calls == 1
    turn = sess.get_context('chat').get('all')[0]
    assert turn['message'] == 'Summarize'
    assert [c['context'].get()['content'] for c in turn['context']] == [
        'First attachment', 'Second attachment']


@pytest.mark.parametrize('args', [[], ['--steps', '3'], ['agent', '--json']])
@pytest.mark.parametrize('stdin_flag', ['-f', '--stdin-message'])
def test_stdin_task_is_consumed_once(harness, args, stdin_flag):
    suffix = ['-f', '-'] if stdin_flag == '-f' else ['--stdin-message']
    result = harness[0].invoke(main.cli, [*args, *suffix], input='Task from stdin')
    assert result.exit_code == 0, result.output
    turn = harness[1][0].get_context('chat').get('all')[0]
    assert turn['message'] == 'Task from stdin'
    assert not turn.get('context')


def test_explicit_task_with_stdin_attachment_preserves_both(harness):
    result = harness[0].invoke(main.cli, ['agent', '--json', '--message', 'Summarize', '-f', '-'],
                               input='Document content')
    assert result.exit_code == 0, result.output
    turn = harness[1][0].get_context('chat').get('all')[0]
    assert turn['message'] == 'Summarize'
    assert turn['context'][0]['context'].get()['content'] == 'Document content'


@pytest.mark.parametrize('args', [[], ['--steps', '3'], ['agent', '--json']])
def test_missing_file_fails_before_provider_call(harness, tmp_path, args):
    result = harness[0].invoke(main.cli, [*args, '-f', str(tmp_path / 'missing.txt')])
    assert result.exit_code == 1
    assert harness[1][0].provider.calls == 0
    if '--json' in args:
        assert json.loads(result.stdout)['status'] == 'failed'
    else:
        assert result.stdout == ''


def test_plain_final_output_only_contains_answer(harness):
    result = harness[0].invoke(main.cli, ['--message', 'Task'])
    assert result.exit_code == 0
    assert result.stdout == 'Answer\n'
    assert 'provider diagnostic' in result.stderr


@pytest.mark.parametrize('args', [['-s', '--message', 'Task'],
                                  ['--agent-output', 'full', 'agent', '--message', 'Task']])
def test_stream_errors_keep_partial_output_but_exit_nonzero(harness, args):
    harness[2]['exception'] = True
    result = harness[0].invoke(main.cli, args)
    assert result.exit_code == 1
    assert 'Answer' in result.stdout
    assert 'stream interrupted' in result.stderr


def test_snapshot_cli_options_override_snapshot_params(harness):
    snapshot = {'params': {'steps': 2, 'agent_writes': 'allow'},
                'contexts': {'file': [{'data': {'name': 'stdin', 'content': 'Snapshot task'}}]}}
    result = harness[0].invoke(main.cli, ['--steps', '3', '--agent-writes', 'deny', 'agent',
                                         '--json', '--from-stdin', '--no-hooks'],
                               input=json.dumps(snapshot))
    assert result.exit_code == 0, result.output
    sess = harness[1][0]
    assert sess.params['steps'] == 3
    assert sess.get_flag('hooks_disabled')
    assert sess.get_context('chat').get('all')[0]['message'] == 'Snapshot task'
    assert 'File writes are disabled' in sess.get_context('prompt').get()['content']


@pytest.mark.parametrize('stdin', ['[]', '{broken', ''])
def test_invalid_snapshot_is_json_failure(harness, stdin):
    result = harness[0].invoke(main.cli, ['agent', '--json', '--from-stdin'], input=stdin)
    assert result.exit_code == 1
    assert json.loads(result.stdout)['status'] == 'failed'
    assert not harness[1]


def test_stdin_message_conflicts_with_stdin_file(harness):
    result = harness[0].invoke(main.cli, ['agent', '--json', '--stdin-message', '-f', '-'],
                               input='Task')
    assert result.exit_code == 1
    assert 'cannot be combined' in json.loads(result.stdout)['error']
    assert not harness[1]


def test_completion_does_not_execute_pseudo_tools(harness, monkeypatch):
    original = Session.get_action
    commands = SimpleNamespace(parse_commands=lambda text: ['cmd'],
                               run=lambda text: pytest.fail('Completion executed a tool'))
    monkeypatch.setattr(Session, 'get_action', lambda self, name: commands
                        if name == 'assistant_commands' else original(self, name))
    harness[2]['text'] = '%%CMD%% command="cmd" arguments="echo hi"\n%%END%%'
    result = harness[0].invoke(main.cli, ['--message', 'Task'])
    assert result.exit_code == 0, result.output


def test_snapshot_full_output_streams_once(harness):
    snapshot = {'contexts': {'file': [{'data': {'name': 'stdin', 'content': 'Task'}}]}}
    result = harness[0].invoke(main.cli, ['--agent-output', 'full', 'agent', '--from-stdin'],
                               input=json.dumps(snapshot))
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == 'Answer'
    assert harness[1][0].provider.calls == 1


def test_json_builder_failure_is_one_failure_object(harness, monkeypatch):
    def fail_build(*args, **kwargs):
        raise RuntimeError('initialization failed')
    monkeypatch.setattr(main.SessionBuilder, 'build', fail_build)
    result, data = invoke_json(harness)
    assert result.exit_code == 1
    assert data['error'] == 'initialization failed'
    assert data['turns'] == 0


def test_failed_context_creation_is_not_ignored(harness, monkeypatch):
    original = Session.add_context
    monkeypatch.setattr(Session, 'add_context', lambda self, kind, value=None:
                        None if kind == 'file' else original(self, kind, value))
    result = harness[0].invoke(main.cli, ['agent', '--json', '-f', 'unreadable.txt'])
    assert result.exit_code == 1
    assert 'Could not load file context' in json.loads(result.stdout)['error']
    assert harness[1][0].provider.calls == 0


def test_raw_completion_outputs_serializable_provider_response(harness):
    result = harness[0].invoke(main.cli, ['--raw', '--message', 'Task'])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {'choices': ['Answer']}


def test_no_provider_is_a_failure(harness, monkeypatch):
    monkeypatch.setattr(Session, 'get_provider', lambda self: None)
    result, data = invoke_json(harness)
    assert result.exit_code == 1
    assert data['error'] == 'No provider available'


def test_output_none_remains_quiet_but_reports_failure(harness):
    result = harness[0].invoke(main.cli, ['--agent-output', 'none', 'agent', '--message', 'Task'])
    assert result.exit_code == 0
    assert result.stdout == ''
    harness[2]['exception'] = True
    result = harness[0].invoke(main.cli, ['--agent-output', 'none', 'agent', '--message', 'Task'])
    assert result.exit_code == 1
    assert result.stdout == ''
    assert 'backend unavailable' in result.stderr
