"""Inert experiment selection and durable task ownership."""

from dataclasses import asdict
import json
import sys

import pytest

from app.project import resolve_project
from workspace.experiment import (ExecutionProfile, Strategy, load_profile, load_strategy,
                                  snapshot, validate_experiment)
from workspace.state import WorkspaceStore
from workspace.task_runner import TaskRunner
from workspace.tasks import TaskStore


def test_strategy_cannot_supply_execution_authority(tmp_path):
    source = tmp_path / 'strategy.toml'
    source.write_text('name = "Explore"\ncalls = 9000\n')
    with pytest.raises(ValueError, match='Unsupported'):
        load_strategy(source)
    source.write_text('name = "Explore"\nprompt = "Follow evidence wherever it leads."\n')
    strategy, origin = load_strategy(source)
    assert strategy.workflow == 'session'
    assert origin['sha256']


def test_profile_precedence_and_strict_limits(tmp_path):
    source = tmp_path / 'profile.toml'
    source.write_text('model = "local"\ncalls = 8\ntools = ["file"]\n')
    profile, origin = load_profile(source, defaults={'model': 'default'}, overrides={'model': 'explicit'})
    assert profile.model == 'explicit' and profile.calls == 8
    with pytest.raises(ValueError):
        ExecutionProfile(timeout=float('nan')).validate()
    with pytest.raises(ValueError):
        validate_experiment(Strategy(workflow='fixed', agents=3), ExecutionProfile(concurrency=2))


def test_task_lease_rejects_duplicate_owner(tmp_path):
    tasks = TaskStore(WorkspaceStore(tmp_path))
    task = tasks.create('Brief', {})
    with tasks.lease(task['id']):
        with pytest.raises(ValueError, match='owner'):
            with TaskStore(tasks.workspace).lease(task['id']):
                pass
    with tasks.lease(task['id']):
        assert tasks.read(task['id'])['brief'] == 'Brief'


WORKER = '''
import json, pathlib, sys
spec = json.load(sys.stdin)
root = pathlib.Path(spec['directory']) / 'runs' / spec['run_id']
(root / 'task-result.json').write_text(json.dumps({'status': 'completed', 'answer': 'Answer'}))
'''


def test_task_repeat_keeps_identity_and_creates_new_attempt(tmp_path):
    runner = TaskRunner(WorkspaceStore(tmp_path / 'records'), worker_command=[sys.executable, '-c', WORKER])
    task = runner.create('Brief', resolve_project(tmp_path), snapshot(
        Strategy(), ExecutionProfile(model='offline')))
    first = runner.run(task['id'])
    second = runner.run(task['id'])
    assert first['task_id'] == second['task_id']
    assert first['run_id'] != second['run_id']
    assert second['answer'] == 'Answer'
    record = runner.tasks.read(task['id'])
    assert len(record['attempts']) == 2
    assert all(a['status'] == 'completed' for a in record['attempts'])


def test_supervisor_times_out_and_releases_lease(tmp_path):
    runner = TaskRunner(WorkspaceStore(tmp_path / 'records'),
                        worker_command=[sys.executable, '-c', 'import time; time.sleep(30)'])
    task = runner.create('Brief', resolve_project(tmp_path), snapshot(
        Strategy(), ExecutionProfile(model='offline', timeout=0.1)))
    result = runner.run(task['id'])
    assert result['status'] == 'timed_out'
    assert runner.tasks.read(task['id'])['attempts'][0]['status'] == 'timed_out'
    with runner.tasks.lease(task['id']):
        pass


def test_failed_result_is_not_reported_as_missing_result(tmp_path):
    script = WORKER.replace("'completed', 'answer': 'Answer'", "'failed', 'answer': 'Provider failed'")
    runner = TaskRunner(WorkspaceStore(tmp_path / 'records'), worker_command=[sys.executable, '-c', script])
    task = runner.create('Brief', resolve_project(tmp_path), snapshot(Strategy(), ExecutionProfile()))
    result = runner.run(task['id'])
    assert result['status'] == 'failed'
    assert 'without a result' not in result['error']


@pytest.mark.parametrize('workflow', ['fixed', 'adaptive', 'single'])
def test_legacy_experiments_use_profiles_without_owning_task_policy(tmp_path, monkeypatch, workflow):
    from workspace.task_worker import run_legacy
    from workspace.engine import WorkspaceEngine
    from workspace.adaptive import AdaptiveEngine
    store = WorkspaceStore(tmp_path / 'records')
    run = store.create_run('Brief', {})
    def fake_run(self, question, on_started):
        assert self.settings.model == 'offline'
        assert self.settings.synthesis_model == 'synth'
        assert self.settings.repository == str(tmp_path)
        assert not self.new_process_group
        if workflow == 'fixed':
            assert self.settings.steps * self.settings.agents + 1 <= 8
        else:
            assert self.total_calls == 8
        on_started({'id': 'experiment'})
        return {'id': 'experiment', 'status': 'completed', 'answer': 'Answer'}
    monkeypatch.setattr(WorkspaceEngine, 'run', fake_run)
    monkeypatch.setattr(AdaptiveEngine, 'run', fake_run)
    import time
    strategy, _ = load_strategy(workflow)
    result = run_legacy({'run_id': run['id'], 'project': {'root': str(tmp_path)},
                         'profile': asdict(ExecutionProfile(model='offline', synthesis_model='synth', calls=8)),
                         'strategy': asdict(strategy), 'deadline_monotonic': time.monotonic() + 30,
                         'brief': 'Brief'}, store)
    assert result['experiment_run_id'] == 'experiment'


def test_task_store_cannot_mix_project_notes(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    runner = TaskRunner(WorkspaceStore(tmp_path / 'records'))
    runner.create('Brief', resolve_project(first), snapshot(Strategy(), ExecutionProfile()))
    with pytest.raises(ValueError, match='another project'):
        runner.create('Different brief', resolve_project(second), snapshot(Strategy(), ExecutionProfile()))


def test_existing_store_adoption_checks_repository(tmp_path):
    store = WorkspaceStore(tmp_path / 'records')
    store.create_run('Earlier', {'repository': str(tmp_path / 'different')})
    with pytest.raises(ValueError, match='another project'):
        TaskRunner(store).create('Brief', resolve_project(tmp_path), snapshot(Strategy(), ExecutionProfile()))
