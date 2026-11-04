"""Task service behavior through real assembly/runtime and an offline provider."""

from dataclasses import replace
import time

from component_registry import ComponentRegistry
from app.project import resolve_project
from workspace.experiment import ExecutionProfile, Strategy
from workspace.state import WorkspaceStore, read_json
from workspace.task_service import TaskService


def make_service(builder, tmp_path, profile=None):
    profile = profile or ExecutionProfile(model='worker-model', tools=('file',), calls=12)
    store = WorkspaceStore(tmp_path / 'records')
    record = store.create_run('Task', {'kind': 'task'})
    return TaskService(store, record['id'], builder, resolve_project(tmp_path), profile,
                       'Use the provided tools to solve the task.')


def finish(service, root):
    deadline = time.monotonic() + 5
    while not service._agents[root]['done'].wait(0.05):
        assert time.monotonic() < deadline
    return service.status(root)


class DelegatingProvider:
    def __init__(self, session):
        self.session = session
        self.calls = 0
        self.tools = []

    def chat(self):
        self.calls += 1
        self.tools = []
        caller = self.session.agents.agent_id
        if caller == 'agent-2':
            return f'Verified evidence generation {self.calls}'
        if self.calls == 1:
            arguments = {'operation': 'spawn', 'message': 'Find evidence'}
        elif self.calls in (2, 4):
            arguments = {'operation': 'wait', 'target': 'agent-2', 'seconds': 2}
        elif self.calls == 3:
            arguments = {'operation': 'followup', 'target': 'agent-2', 'message': 'Check again'}
        else:
            return 'Answer based on both contributions'
        self.tools = [{'id': str(self.calls), 'name': 'agents', 'arguments': arguments}]
        return ''

    def get_tool_calls(self):
        return self.tools


def test_model_directed_spawn_followup_and_shared_budget(builder, tmp_path, monkeypatch):
    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda self, name: DelegatingProvider)
    service = make_service(builder, tmp_path)
    try:
        root = service.start('Investigate')
        state = finish(service, root)
        assert state['agents'][0]['status'] == 'completed', state
        assert state['calls_used'] == 7
        child = service._agents['agent-2']
        assert child['generations'] == 2
        assert child['host'].session.provider.calls == 2
        assert len(child['outcomes']) == 2
        assert all(out['status'] == 'completed' for out in child['outcomes'])
        root_chat = service._agents[root]['host'].session.get_context('chat').get('all')
        assert 'generation 2' in str(root_chat)
        assert child['host'].session.project == service.project
        saved = read_json(service.store.run_dir(service.run_id) / 'task-state.json')
        assert saved['calls_used'] == 7
        assert len(service.store.notes(run_id=service.run_id)) == 3
    finally:
        service.close()


def test_internal_helper_calls_share_task_allowance(builder, tmp_path, monkeypatch):
    class HelperProvider:
        def __init__(self, session):
            self.session = session
        def chat(self):
            if getattr(self.session, 'agents', None):
                result = self.session.run_internal_completion(message='Selected helper task')
                return f'Helper status: {result.status}'
            return 'Helper evidence'
    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda self, name: HelperProvider)
    service = make_service(builder, tmp_path, ExecutionProfile(model='worker-model', calls=1))
    try:
        root = service.start('Use helper')
        state = finish(service, root)
        assert state['calls_used'] == 1
        assert 'incomplete' in state['agents'][0]['result']
    finally:
        service.close()


def test_agent_tool_cannot_change_scope(builder, tmp_path):
    service = make_service(builder, tmp_path)
    try:
        root = service.start('Task')
        finish(service, root)
        session = service._agents[root]['host'].session
        action = session.get_action('assistant_workspace_agents_tool')
        result = action.start({'operation': 'spawn', 'message': 'Escape', 'base_directory': '/'})
        assert not result.payload['ok']
        assert len(service._agents) == 1
        commands = session.get_action('assistant_commands').commands
        assert 'memory' not in commands and 'ragsearch' not in commands
    finally:
        service.close()


def test_writable_project_does_not_allow_editing_control_records(builder, tmp_path):
    service = make_service(builder, tmp_path, ExecutionProfile(model='worker-model', writes='allow'))
    try:
        root = service.start('Task')
        finish(service, root)
        session = service._agents[root]['host'].session
        fs = session.get_action('assistant_fs_handler')
        assert fs.validate_path(str(tmp_path / 'site.html'), operation='write')
        for directory in ('tasks', 'runs', 'notes'):
            (service.store.root / directory).mkdir(exist_ok=True)
            assert fs.validate_path(str(service.store.root / directory / 'record.json'),
                                    operation='write') is None
        assert fs.validate_path(str(service.store.run_dir(service.run_id) /
                                    'questions' / 'question-1' / 'reply.json'),
                                operation='write') is None
        assert not fs.delete_directory(str(service.store.root), recursive=True, force=True)
        assert not fs.rename(str(service.store.root), str(tmp_path / 'moved'), force=True)
        docker = session.get_action('assistant_docker_tool')
        mounts = docker._mount_args()
        assert f'{service.store.root / "runs"}:/workspace/records/runs:ro' in mounts
        assert service.store.publish(service.run_id, root, 'Finding', 'Host-owned publication', [])
    finally:
        service.close()


def test_task_cli_uses_explicit_project_and_saves_effective_choices(builder, tmp_path, monkeypatch):
    import json
    import sys
    import main
    from click.testing import CliRunner
    from workspace.task_runner import TaskRunner
    import modes.workspace_task_commands as commands
    from workspace.tasks import TaskStore
    project = tmp_path / 'project'
    project.mkdir()
    brief = tmp_path / 'brief.md'
    brief.write_text('Inspect the business brief')
    strategy = tmp_path / 'strategy.toml'
    strategy.write_text('name = "Different approach"\nprompt = "Investigate carefully"\n')
    profile = tmp_path / 'profile.toml'
    profile.write_text('model = "explicit-model"\ncalls = 4\ntools = ["file"]\n')
    script = '''
import json, pathlib, sys
spec=json.load(sys.stdin)
root=pathlib.Path(spec['directory'])/'runs'/spec['run_id']
(root/'task-result.json').write_text(json.dumps({'status':'completed','answer':'Answer'}))
'''
    monkeypatch.setattr(main, 'ConfigManager', lambda conf: builder.config_manager)
    monkeypatch.setattr(commands, 'TaskRunner', lambda store: TaskRunner(
        store, worker_command=[sys.executable, '-c', script]))
    result = CliRunner().invoke(main.cli, ['--project', str(project), 'workspace', 'task', 'start',
                                           '--brief', str(brief), '--strategy', str(strategy),
                                           '--profile', str(profile), '--json'])
    assert result.exit_code == 0, result.output
    tasks = TaskStore(WorkspaceStore(project / '.memex-workspace')).list()
    assert len(tasks) == 1
    record = tasks[0]
    assert record['spec']['profile']['model'] == 'explicit-model'
    assert record['spec']['profile']['calls'] == 4
    assert record['spec']['strategy']['name'] == 'Different approach'
    assert record['spec']['project']['root'] == str(project)
    assert record['brief'] == brief.read_text()


def test_task_helpers_inherit_selected_model_and_cannot_switch_profiles(builder, tmp_path, monkeypatch):
    import pytest
    from app.run_helpers import child_options
    models = []
    class Provider:
        def __init__(self, session):
            self.session = session
        def chat(self):
            models.append(self.session.get_params()['model'])
            if getattr(self.session, 'agents', None):
                return self.session.run_internal_completion(message='Helper').last_text
            return 'Evidence'
    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda self, name: Provider)
    service = make_service(builder, tmp_path, ExecutionProfile(model='explicit-model'))
    try:
        root = service.start('Task')
        finish(service, root)
        assert models == ['explicit-model', 'explicit-model']
        assert service.status()['calls_used'] == 2
        with pytest.raises(ValueError, match='model'):
            child_options(service._agents[root]['host'].session, {'model': 'worker-model'})
    finally:
        service.close()


def test_agent_question_reaches_user_and_continues(builder, tmp_path, monkeypatch):
    from workspace.questions import Questions
    class AskingProvider(DelegatingProvider):
        def chat(self):
            self.calls += 1
            self.tools = []
            if self.calls == 1:
                self.tools = [{'id': 'ask-1', 'name': 'agents', 'arguments': {
                    'operation': 'ask', 'message': 'Which source?'}}]
                return ''
            return 'Answer after user reply'
    monkeypatch.setattr(ComponentRegistry, 'load_provider_class', lambda self, name: AskingProvider)
    service = make_service(builder, tmp_path)
    service.user_input = True
    try:
        root = service.start('Investigate')
        questions = Questions(service.store, service.run_id)
        until = time.monotonic() + 3
        while not questions.pending():
            assert time.monotonic() < until
            time.sleep(.01)
        question = questions.pending()[0]
        questions.reply(question['id'], 'Primary sources')
        state = finish(service, root)
        assert state['agents'][0]['status'] == 'completed', state
        chat = service._agents[root]['host'].session.get_context('chat').get('all')
        assert 'Primary sources' in str(chat)
    finally:
        service.close()


def test_chat_workspace_commands_dispatch_and_remain_opt_in(builder, tmp_path, monkeypatch):
    from workspace.chat import ChatWorkspace
    session = builder.build(mode='internal', project_context=resolve_project(tmp_path))
    try:
        from types import SimpleNamespace
        session.ui = SimpleNamespace(emit=lambda *args: None)
        action = session.get_action('chat_workspace')
        assert not action.run(['attach', str(tmp_path / 'records')]).payload['ok']
        session.cli_workspace_enabled = True
        session.chat_config_file = None
        commands = session.get_action('chat_commands')
        assert commands.run('/workspace attach ' + str(tmp_path / 'records'))
        assert session.chat_workspace.project.root == tmp_path
        started = []
        def start(self, brief, **kwargs):
            started.append((brief, kwargs))
            return 'task-example'
        monkeypatch.setattr(ChatWorkspace, 'start', start)
        assert commands.run('/workspace start --profile local.toml Check retries')
        assert started == [('Check retries', {'detached': False, 'profile': 'local.toml'})]
        assert commands.run('/workspace detach --chat-last 4 --profile local.toml Check again')
        assert started[-1] == ('Check again', {
            'detached': True, 'profile': 'local.toml', 'chat_last': 4})
    finally:
        session.close()
