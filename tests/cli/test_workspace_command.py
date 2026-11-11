from __future__ import annotations

import configparser
import json
from types import SimpleNamespace

from click.testing import CliRunner

import main
from workspace.engine import WorkspaceEngine


def configure(monkeypatch, tmp_path, *, synthesis_model=''):
    class Config:
        def __init__(self, _conf=None):
            self.base_config = configparser.ConfigParser()
            self.base_config['AGENT'] = {'default_model': 'glm-5.3'}
            self.base_config['TOOLS'] = {'base_directory': str(tmp_path)}
            self.base_config['WORKSPACE'] = {'agents': '2', 'max_steps_per_agent': '12'}
            self.base_config['WORKSPACE']['synthesis_model'] = synthesis_model

        def create_session_config(self, *_args):
            return SimpleNamespace(normalize_model_name=lambda name: name if name in
                                   ('glm-5.3', 'other-model') else None)

    monkeypatch.setattr(main, 'ConfigManager', Config)
    captured = {}

    def run(self, question, **_kwargs):
        captured.update(question=question, settings=self.settings, overrides=self.overrides)
        return {'id': 'test-run', 'status': 'completed', 'answer': 'Result',
                'workers': [], 'directory': str(tmp_path)}

    monkeypatch.setattr(WorkspaceEngine, 'run', run)
    return captured


def test_workspace_run_inherits_default_and_accepts_question(monkeypatch, tmp_path):
    captured = configure(monkeypatch, tmp_path)
    result = CliRunner().invoke(main.cli, ['workspace', 'run', 'Find model selection'])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == 'Result'
    assert captured['question'] == 'Find model selection'
    assert captured['settings'].model is None  # SessionBuilder selects AGENT.default_model.
    assert captured['settings'].agents == 2
    assert captured['settings'].writes_policy == 'deny'
    assert captured['overrides']['use_mcp'] is False
    assert 'max_tokens' not in captured['overrides']


def test_research_accepts_a_brief_and_attachments_with_one_shared_budget(monkeypatch, tmp_path):
    from workspace.adaptive import AdaptiveEngine

    configure(monkeypatch, tmp_path)
    brief = tmp_path / 'brief.md'
    brief.write_text('Original research brief')
    attachment = tmp_path / 'source.txt'
    attachment.write_text('Supporting evidence')
    captured = {}

    def run(self, question, **kwargs):
        captured.update(question=question, calls=self.total_calls, strategy=self.strategy,
                        contexts=self.contexts, settings=self.settings)
        return dict(status='completed', answer='Research answer')

    monkeypatch.setattr(AdaptiveEngine, 'run', run)
    result = CliRunner().invoke(main.cli, [
        '-m', 'other-model', 'workspace', 'research', '--brief', str(brief),
        '-f', str(attachment), '--calls', '21', '--strategy', 'single',
    ])
    assert result.exit_code == 0, result.output
    assert captured['question'] == 'Original research brief'
    assert captured['calls'] == 21 and captured['strategy'] == 'single'
    assert captured['contexts'] == (('file', str(attachment)),)
    assert captured['settings'].model == captured['settings'].synthesis_model == 'other-model'
    result = CliRunner().invoke(main.cli, [
        'workspace', 'research', 'Another brief', '--brief', str(brief),
    ])
    assert result.exit_code == 2 and 'once' in result.output
    result = CliRunner().invoke(main.cli, [
        '--steps', '12', 'workspace', 'research', '--brief', str(brief),
    ])
    assert result.exit_code == 2 and '--calls' in result.output


def test_explicit_model_tools_and_limits_reach_workers(monkeypatch, tmp_path):
    captured = configure(monkeypatch, tmp_path)
    result = CliRunner().invoke(main.cli, [
        '-m', 'other-model', '--tools', 'file', '--steps', '4', 'workspace', 'run',
        '--agents', '3', '--json', '--message', 'Question',
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['answer'] == 'Result'
    assert captured['settings'].model == 'other-model'
    assert captured['settings'].steps == 4
    assert captured['settings'].agents == 3
    assert captured['overrides']['active_tools_agent'] == 'file'


def test_question_input_conflicts_are_reported(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    runner = CliRunner()
    assert runner.invoke(main.cli, ['workspace', 'run']).exit_code == 2
    result = runner.invoke(main.cli, ['workspace', 'run', 'one', '--message', 'two'])
    assert result.exit_code == 2
    assert 'once' in result.output
    result = runner.invoke(main.cli, ['workspace', 'run', '--stdin-message'], input='Question')
    assert result.exit_code == 0


def test_status_reads_saved_state_without_constructing_an_engine(monkeypatch, tmp_path):
    from workspace.state import WorkspaceStore

    configure(monkeypatch, tmp_path)
    store = WorkspaceStore(tmp_path / '.memex-workspace')
    run = store.create_run('Question', {})
    store.update_run(run['id'], status='completed', phase='finished')

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Monitoring must not construct an engine')

    monkeypatch.setattr(WorkspaceEngine, '__init__', forbidden)
    result = CliRunner().invoke(main.cli, ['workspace', 'status', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['question'] == 'Question'
    result = CliRunner().invoke(main.cli, ['workspace', 'report', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['question'] == 'Question'


def test_synthesis_replay_uses_explicit_model_prompt_and_standalone_timeout(monkeypatch, tmp_path):
    captured = configure(monkeypatch, tmp_path)

    def synthesize(self, source, **_kwargs):
        captured.update(source=source, settings=self.settings, overrides=self.overrides)
        return {'status': 'completed', 'answer': 'Replayed answer'}

    monkeypatch.setattr(WorkspaceEngine, 'synthesize', synthesize)
    result = CliRunner().invoke(main.cli, [
        '-m', 'other-model', '-p', 'Alternate prompt', '-l', '10000',
        'workspace', 'synthesize', 'original-run', '--timeout', '20',
    ])
    assert result.exit_code == 0, result.output
    assert captured['source'] == 'original-run'
    assert captured['settings'].model == 'other-model'
    assert captured['settings'].synthesis_model is None
    assert captured['settings'].synthesis_prompt == 'Alternate prompt'
    assert captured['settings'].run_timeout_seconds == 20
    assert captured['overrides']['max_tokens'] == 10000


def test_token_limits_inherit_models_unless_explicitly_overridden(monkeypatch, tmp_path):
    from modes.workspace_mode import _engine
    from workspace.state import WorkspaceStore
    from workspace.engine import WorkspaceSettings

    configure(monkeypatch, tmp_path)
    cfg = configparser.ConfigParser(defaults={'max_tokens': '8192'})
    cfg['WORKSPACE'] = {}
    manager = SimpleNamespace(base_config=cfg, create_session_config=lambda: SimpleNamespace(
        normalize_model_name=lambda name: name))
    ctx = SimpleNamespace(obj={'CONFIG_MANAGER': manager})
    settings = WorkspaceSettings(repository=str(tmp_path), model='other-model')
    store = WorkspaceStore(tmp_path)
    assert 'max_tokens' not in _engine(ctx, store, settings, {}).overrides
    cfg['WORKSPACE']['max_tokens'] = '12000'
    assert _engine(ctx, store, settings, {}).overrides['max_tokens'] == 12000
    assert _engine(ctx, store, settings, {'max_tokens': 20000}).overrides['max_tokens'] == 20000


def test_explicit_model_wins_over_configured_synthesis_default(monkeypatch, tmp_path):
    captured = configure(monkeypatch, tmp_path, synthesis_model='other-model')
    runner = CliRunner()
    assert runner.invoke(main.cli, ['workspace', 'run', 'Question']).exit_code == 0
    assert captured['settings'].synthesis_model == 'other-model'
    assert runner.invoke(main.cli, ['-m', 'glm-5.3', 'workspace', 'run', 'Question']).exit_code == 0
    assert captured['settings'].model == 'glm-5.3'
    assert captured['settings'].synthesis_model is None
    assert runner.invoke(main.cli, ['-m', 'glm-5.3', 'workspace', 'run', 'Question',
                                    '--synthesis-model', 'other-model']).exit_code == 0
    assert captured['settings'].synthesis_model == 'other-model'


def test_watch_once_and_cancel_are_model_free(monkeypatch, tmp_path):
    from workspace.state import WorkspaceStore, now
    from workspace.tasks import TaskStore
    configure(monkeypatch, tmp_path)
    store = WorkspaceStore(tmp_path / '.memex-workspace')
    tasks = TaskStore(store)
    task = tasks.create('Inspect retries', {'strategy': {'workflow': 'session'}})
    run = store.create_run('Inspect retries', {'task_id': task['id']})
    task.update(status='running', attempts=[{'run_id': run['id'], 'started_at': now()}])
    tasks.save(task)
    store.event(run['id'], 'agent-1', 'tool_started', {
        'name': 'cmd', 'args': {'command': 'rg', 'arguments': 'retry'}})
    with tasks.lease(task['id']):
        result = CliRunner().invoke(main.cli, ['workspace', 'watch', task['id'], '--once'])
        assert result.exit_code == 0, result.output
        assert 'Tool cmd: command=rg arguments=retry' in result.output
        assert not (tasks.directory(task['id']) / 'cancel').exists()
        result = CliRunner().invoke(main.cli, ['workspace', 'task', 'cancel', task['id']])
        assert result.exit_code == 0, result.output
        assert (tasks.directory(task['id']) / 'cancel').exists()


def test_watch_ctrl_c_does_not_cancel_task(monkeypatch, tmp_path):
    from workspace.state import WorkspaceStore
    from workspace.tasks import TaskStore
    configure(monkeypatch, tmp_path)
    tasks = TaskStore(WorkspaceStore(tmp_path / '.memex-workspace'))
    task = tasks.create('Waiting for launch', {})
    def interrupt(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr('modes.workspace_watch_commands.time.sleep', interrupt)
    result = CliRunner().invoke(main.cli, ['workspace', 'watch', task['id']])
    assert result.exit_code == 0, result.output
    assert 'task execution was not changed' in result.output
    assert tasks.read(task['id'])['status'] == 'pending'
    assert not (tasks.directory(task['id']) / 'cancel').exists()


def test_watch_follows_new_events_and_exits_after_completion(monkeypatch, tmp_path):
    from workspace.state import WorkspaceStore, now, write_json
    from workspace.tasks import TaskStore
    configure(monkeypatch, tmp_path)
    store = WorkspaceStore(tmp_path / '.memex-workspace')
    tasks = TaskStore(store)
    task = tasks.create('Inspect retries', {})
    run = store.create_run(task['brief'], {'task_id': task['id']})
    task.update(status='running', attempts=[{'run_id': run['id'], 'started_at': now()}])
    tasks.save(task)
    count = 0
    def advance(_):
        nonlocal count
        count += 1
        if count == 1:
            store.event(run['id'], 'agent-2', 'tool_started', {
                'name': 'websearch', 'args': {'query': 'retry guidance'}})
        elif count == 2:
            store.event(run['id'], 'agent-2', 'tool_finished', {
                'name': 'websearch', 'status': 'completed', 'result': {'ok': True}})
            task['status'] = 'completed'
            tasks.save(task)
            write_json(store.run_dir(run['id']) / 'task-result.json', {'answer': 'Done'})
        else:
            raise AssertionError('Watcher failed to exit')
    monkeypatch.setattr('modes.workspace_watch_commands.time.sleep', advance)
    with tasks.lease(task['id']):
        result = CliRunner().invoke(main.cli, ['workspace', 'watch', task['id'], '--tail', '0'])
    assert result.exit_code == 0, result.output
    assert result.output.count('query=retry guidance') == 1
    assert 'Tool websearch: completed' in result.output
    assert 'Result: Done' in result.output
    assert count == 2
