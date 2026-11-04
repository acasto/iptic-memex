"""Offline scheduling tests with real task supervision and fake worker processes."""
from datetime import datetime, timezone
import json
import sys

import pytest

from app.project import resolve_project
from workspace.experiment import ExecutionProfile, Strategy, snapshot
from workspace.schedules import ScheduleStore, Scheduler, timestamp
from workspace.state import WorkspaceStore, read_json, write_json
from workspace.task_runner import TaskRunner


WORKER = '''
import json, pathlib, sys
spec = json.load(sys.stdin)
assert spec['user_input'] is False
assert spec['execution_origin']['occurrence_id']
root = pathlib.Path(spec['directory']) / 'runs' / spec['run_id']
(root / 'task-result.json').write_text(json.dumps({'status': 'completed', 'answer': 'Offline result'}))
'''


@pytest.fixture
def setup(tmp_path):
    store = WorkspaceStore(tmp_path / 'records')
    runner = TaskRunner(store, worker_command=[sys.executable, '-c', WORKER])
    experiment = snapshot(Strategy(), ExecutionProfile(model='offline', timeout=1))
    experiment['user_input'] = True
    task = runner.create('Brief', resolve_project(tmp_path), experiment)
    schedules = ScheduleStore(store, tmp_path)
    scheduler = Scheduler(schedules, runner=runner,
                          clock=lambda: datetime(2026, 11, 4, 12, tzinfo=timezone.utc))
    return schedules, scheduler, task


def add(schedules, task):
    return schedules.add(task['id'], '2026-11-04T10:00:00Z')


def test_run_once_with_provenance_and_no_replay(setup):
    schedules, scheduler, task = setup
    record = add(schedules, task)
    result = scheduler.run_due()
    assert result['status'] == 'completed'
    assert len(result['runs']) == 1
    saved = schedules.read(record['id'])
    assert saved['status'] == 'completed' and not saved['enabled']
    run_id = saved['occurrence']['run_id']
    run = read_json(schedules.workspace.run_dir(run_id) / 'run.json')
    assert run['execution_origin']['occurrence_id'] == record['occurrence']['id']
    assert schedules.tasks.read(task['id'])['spec']['user_input'] is True
    assert scheduler.run_due()['status'] == 'idle'
    assert len(schedules.tasks.read(task['id'])['attempts']) == 1


def test_idle_and_dry_run_do_not_write(setup, tmp_path):
    schedules, scheduler, task = setup
    assert scheduler.run_due()['status'] == 'idle'
    assert not schedules.root.exists()
    record = add(schedules, task)
    before = {p: p.read_bytes() for p in schedules.workspace.root.rglob('*') if p.is_file()}
    assert scheduler.run_due(dry_run=True)['due'] == [record['id']]
    assert {p: p.read_bytes() for p in schedules.workspace.root.rglob('*') if p.is_file()} == before
    assert not (schedules.root / 'dispatch.lock').exists()


def test_future_pause_resume_and_explicit_offset(setup):
    schedules, scheduler, task = setup
    with pytest.raises(ValueError, match='offset'):
        schedules.add(task['id'], '2026-11-04T10:00:00')
    assert timestamp('2026-11-04T05:00:00-05:00') == timestamp('2026-11-04T10:00:00Z')
    future = schedules.add(task['id'], '2026-11-05T10:00:00Z')
    record = add(schedules, task)
    schedules.set_enabled(record['id'], False)
    assert scheduler.run_due()['status'] == 'idle'
    schedules.set_enabled(record['id'], True)
    assert scheduler.run_due()['status'] == 'completed'
    assert schedules.read(future['id'])['status'] == 'scheduled'
    with pytest.raises(ValueError, match='unconsumed'):
        schedules.set_enabled(record['id'], True)


def test_overlapping_tick_and_manual_task_lease(setup):
    schedules, scheduler, task = setup
    record = add(schedules, task)
    with schedules.lock(schedules.root / 'dispatch.lock'):
        assert scheduler.run_due()['status'] == 'busy'
    with schedules.tasks.lease(task['id']):
        result = scheduler.run_due()
        assert result['status'] == 'deferred'
    assert schedules.read(record['id'])['occurrence']['status'] == 'pending'
    assert schedules.tasks.read(task['id'])['attempts'] == []


def test_tick_allowance_cannot_start_over_budget_work(setup):
    schedules, scheduler, task = setup
    record = add(schedules, task)
    assert scheduler.run_due(max_seconds=1)['status'] == 'deferred'
    assert schedules.read(record['id'])['occurrence']['status'] == 'pending'
    assert not schedules.tasks.read(task['id'])['attempts']


def test_changed_inputs_require_review_without_running(setup):
    schedules, scheduler, task = setup
    record = add(schedules, task)
    task['brief'] = 'Different task'
    schedules.tasks.save(task)
    assert scheduler.run_due()['status'] == 'needs_attention'
    assert schedules.read(record['id'])['status'] == 'needs_review'
    assert not schedules.tasks.read(task['id'])['attempts']


def test_claim_without_result_never_replays(setup):
    schedules, scheduler, task = setup
    record = add(schedules, task)
    record.update(status='claimed')
    record['occurrence']['status'] = 'claimed'
    schedules.save(record)
    assert scheduler.run_due()['status'] == 'needs_attention'
    assert schedules.read(record['id'])['status'] == 'needs_review'
    assert not schedules.tasks.read(task['id'])['attempts']


def test_terminal_result_reconciles_crash_before_schedule_finish(setup, monkeypatch):
    schedules, scheduler, task = setup
    record = add(schedules, task)
    finish = scheduler._finish
    def crash(*args, **kwargs):
        raise RuntimeError('Simulated scheduler crash')
    monkeypatch.setattr(scheduler, '_finish', crash)
    with pytest.raises(RuntimeError, match='crash'):
        scheduler.run_due()
    assert schedules.read(record['id'])['occurrence']['status'] == 'claimed'
    monkeypatch.setattr(scheduler, '_finish', finish)
    assert scheduler.run_due()['status'] == 'completed'
    assert schedules.read(record['id'])['status'] == 'completed'
    assert len(schedules.tasks.read(task['id'])['attempts']) == 1


def test_failed_occurrence_consumed_without_retry(setup):
    schedules, scheduler, task = setup
    record = add(schedules, task)
    scheduler.runner.worker_command = [sys.executable, '-c', 'raise SystemExit(1)']
    assert scheduler.run_due()['status'] == 'needs_attention'
    assert schedules.read(record['id'])['occurrence']['outcome'] == 'failed'
    scheduler.run_due()
    assert len(schedules.tasks.read(task['id'])['attempts']) == 1


def test_pause_race_before_claim_is_deferred(setup, monkeypatch):
    schedules, scheduler, task = setup
    record = add(schedules, task)
    original = scheduler.runner.run
    def run(*args, **kwargs):
        schedules.set_enabled(record['id'], False)
        return original(*args, **kwargs)
    monkeypatch.setattr(scheduler.runner, 'run', run)
    assert scheduler.run_due()['status'] == 'deferred'
    assert not schedules.tasks.read(task['id'])['attempts']


def test_live_orphan_claim_blocks_further_dispatch(setup):
    schedules, scheduler, task = setup
    record = add(schedules, task)
    other = add(schedules, task)
    record.update(status='claimed')
    record['occurrence']['status'] = 'claimed'
    schedules.save(record)
    task.update(status='running', attempts=[{'run_id': 'run-test', 'status': 'running'}])
    schedules.tasks.save(task)
    with schedules.tasks.lease(task['id']):
        assert scheduler.run_due(max_runs=2)['status'] == 'busy'
    assert schedules.read(other['id'])['occurrence']['status'] == 'pending'


def test_per_tick_limit_and_cancelled_attempt_stop_dispatch(setup):
    schedules, scheduler, task = setup
    first = add(schedules, task)
    second = add(schedules, task)
    assert len(scheduler.run_due(max_runs=1)['runs']) == 1
    assert len(schedules.tasks.read(task['id'])['attempts']) == 1
    third = add(schedules, task)
    scheduler.runner.worker_command = [sys.executable, '-c', WORKER.replace(
        "'completed'", "'cancelled'")]
    result = scheduler.run_due(max_runs=3)
    assert result['status'] == 'cancelled'
    assert len(result['runs']) == 1
    assert sum(r['occurrence']['status'] == 'pending' for r in schedules.list()) == 1


def test_reconciliation_finds_run_even_before_attempt_link(setup):
    schedules, scheduler, task = setup
    record = add(schedules, task)
    record['status'] = 'claimed'
    record['occurrence']['status'] = 'claimed'
    schedules.save(record)
    run = schedules.workspace.create_run('Brief', {
        'task_id': task['id'], 'execution_origin': {'occurrence_id': record['occurrence']['id']}})
    schedules.workspace.update_run(run['id'], phase='finished', status='completed')
    write_json(schedules.workspace.run_dir(run['id']) / 'task-result.json', {
        'task_id': task['id'], 'run_id': run['id'], 'status': 'completed', 'answer': 'Done'})
    assert scheduler.run_due()['status'] == 'completed'
    assert schedules.read(record['id'])['occurrence']['run_id'] == run['id']
    assert not schedules.tasks.read(task['id'])['attempts']


def test_schedule_path_and_project_binding(setup, tmp_path):
    schedules, scheduler, task = setup
    with pytest.raises(ValueError, match='another project'):
        ScheduleStore(schedules.workspace, tmp_path / 'elsewhere')
    outside = tmp_path / 'outside'
    outside.mkdir()
    schedules.root.mkdir()
    (schedules.root / 'escape').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='escapes'):
        schedules.read('escape')


def test_cli_roundtrip_without_models(setup, monkeypatch, tmp_path):
    import main
    from click.testing import CliRunner
    import modes.workspace_schedule_commands as commands
    schedules, scheduler, task = setup
    monkeypatch.setattr(commands, 'Scheduler', lambda store: scheduler)
    cli = CliRunner()
    base = ['--project', str(tmp_path), 'workspace', 'schedule']
    args = ['--directory', str(schedules.workspace.root)]
    added = cli.invoke(main.cli, base + ['add', task['id'], '--at', '2026-11-04T10:00:00Z'] + args)
    assert added.exit_code == 0, added.output
    record = json.loads(added.output)
    preview = cli.invoke(main.cli, base + ['run-due', '--dry-run'] + args)
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)['due'] == [record['id']]
    result = cli.invoke(main.cli, base + ['run-due'] + args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['status'] == 'completed'
    assert cli.invoke(main.cli, base + ['inspect', record['id']] + args).exit_code == 0
