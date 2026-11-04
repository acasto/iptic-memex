"""Workspace input transport and chat ownership without network/model calls."""
from concurrent.futures import ThreadPoolExecutor
import time
from types import SimpleNamespace

import pytest

from app.project import resolve_project
from core.cancellation import CancellationToken
from core.runtime.contracts import InteractionRequest
from workspace.chat import ChatWorkspace
from workspace.questions import Questions
from workspace.state import WorkspaceStore
from workspace.task_runner import TaskRunner


def test_question_validates_once_and_closes(tmp_path):
    store = WorkspaceStore(tmp_path)
    run = store.create_run('Task', {})
    questions = Questions(store, run['id'])
    token = CancellationToken()
    request = InteractionRequest('question-1', 'bool', {'prompt': 'Continue?'})
    with ThreadPoolExecutor() as pool:
        result = pool.submit(questions.handler(token, time.monotonic() + 3), request)
        until = time.monotonic() + 2
        while not questions.pending():
            assert time.monotonic() < until
            time.sleep(.01)
        with pytest.raises(ValueError, match='boolean'):
            questions.reply(request.id, 'yes')
        questions.reply(request.id, True)
        assert result.result(timeout=2) is True
        with pytest.raises(ValueError):
            questions.reply(request.id, False)
    assert questions.pending() == []


def test_question_wait_cancelled(tmp_path):
    store = WorkspaceStore(tmp_path)
    questions = Questions(store, store.create_run('Task', {})['id'])
    token = CancellationToken()
    token.cancel('Closed')
    with pytest.raises(InterruptedError):
        questions.handler(token, time.monotonic() + 20)(
            InteractionRequest('question-1', 'text', {'prompt': 'Why?'}))
    assert questions.pending() == []


def test_attached_owner_cancels_and_joins(tmp_path, monkeypatch):
    session = SimpleNamespace(project=resolve_project(tmp_path),
                              get_option=lambda *a, **k: 'offline')
    attachment = ChatWorkspace(session, tmp_path / 'records')
    cancelled = []
    def run(self, task_id, *, cancellation):
        while not cancellation.is_cancelled():
            time.sleep(.01)
        cancelled.append(task_id)
    monkeypatch.setattr(TaskRunner, 'run', run)
    task_id = attachment.start('Investigate')
    attachment.close()
    assert cancelled == [task_id]
    assert attachment.tasks.read(task_id)['spec']['user_input'] is True


def test_detached_owner_is_independent_and_cancel_is_durable(tmp_path, monkeypatch):
    import workspace.chat as chat
    calls = []
    class Process:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
        def wait(self):
            return 0
    monkeypatch.setattr(chat.subprocess, 'Popen', Process)
    session = SimpleNamespace(project=resolve_project(tmp_path),
                              get_option=lambda *a, **k: 'offline')
    attachment = ChatWorkspace(session, tmp_path / 'records', config_file='/tmp/custom.ini')
    task_id = attachment.start('Investigate', detached=True)
    attachment.close()
    assert not attachment.owned
    assert calls[0][1]['start_new_session'] is True
    assert 'workspace.task_supervisor' in calls[0][0]
    assert attachment.tasks.read(task_id)['spec']['config_file'] == '/tmp/custom.ini'
    attachment.cancel(task_id)
    assert (attachment.tasks.directory(task_id) / 'cancel').exists()


def test_runner_observes_chat_cancellation(tmp_path):
    import sys
    from workspace.experiment import ExecutionProfile, Strategy, snapshot
    runner = TaskRunner(WorkspaceStore(tmp_path / 'records'), worker_command=[
        sys.executable, '-c', 'import time; time.sleep(30)'])
    task = runner.create('Brief', resolve_project(tmp_path), snapshot(
        Strategy(), ExecutionProfile(model='offline', timeout=30)))
    token = CancellationToken()
    token.cancel('Chat closed')
    result = runner.run(task['id'], cancellation=token)
    assert result['status'] == 'cancelled'
    with runner.tasks.lease(task['id']):
        assert runner.tasks.read(task['id'])['status'] == 'cancelled'


def test_cancel_before_launch_does_not_create_worker(tmp_path, monkeypatch):
    from workspace.experiment import ExecutionProfile, Strategy, snapshot
    runner = TaskRunner(WorkspaceStore(tmp_path / 'records'))
    task = runner.create('Brief', resolve_project(tmp_path), snapshot(Strategy(), ExecutionProfile()))
    def unexpected(*args, **kwargs):
        pytest.fail('Cancelled task launched a worker')
    monkeypatch.setattr('workspace.task_runner.subprocess.Popen', unexpected)
    token = CancellationToken()
    token.cancel('Chat exited during startup')
    assert runner.run(task['id'], cancellation=token)['status'] == 'cancelled'


def test_nonreading_worker_cannot_block_deadline(tmp_path):
    import sys
    from workspace.experiment import ExecutionProfile, Strategy, snapshot
    runner = TaskRunner(WorkspaceStore(tmp_path / 'records'), worker_command=[
        sys.executable, '-c', 'import time; time.sleep(30)'])
    task = runner.create('x' * 500000, resolve_project(tmp_path), snapshot(
        Strategy(), ExecutionProfile(timeout=.1)))
    start = time.monotonic()
    assert runner.run(task['id'])['status'] == 'timed_out'
    assert time.monotonic() - start < 8


def test_inspection_reports_abandoned_owner_without_mutation(tmp_path):
    from workspace.tasks import TaskStore
    tasks = TaskStore(WorkspaceStore(tmp_path / 'records'))
    task = tasks.create('Brief', {})
    task.update(status='running', attempts=[{'run_id': 'run-one', 'status': 'running'}])
    tasks.save(task)
    with tasks.lease(task['id']):
        assert tasks.inspect(task['id'])['status'] == 'running'
    before = (tasks.directory(task['id']) / 'task.json').read_bytes()
    assert tasks.inspect(task['id'])['status'] == 'interrupted'
    assert tasks.inspect(task['id'])['attempts'][0]['status'] == 'interrupted'
    assert (tasks.directory(task['id']) / 'task.json').read_bytes() == before


def test_duplicate_runner_cannot_change_owner_status(tmp_path):
    from workspace.experiment import ExecutionProfile, Strategy, snapshot
    runner = TaskRunner(WorkspaceStore(tmp_path / 'records'))
    task = runner.create('Brief', resolve_project(tmp_path), snapshot(Strategy(), ExecutionProfile()))
    with runner.tasks.lease(task['id']):
        before = runner.tasks.read(task['id'])
        with pytest.raises(ValueError, match='owner'):
            runner.run(task['id'])
        assert runner.tasks.read(task['id']) == before


def test_detached_launch_failure_is_recorded(tmp_path, monkeypatch):
    session = SimpleNamespace(project=resolve_project(tmp_path),
                              get_option=lambda *a, **k: 'offline')
    attachment = ChatWorkspace(session, tmp_path / 'records')
    def fail(*args, **kwargs):
        raise OSError('Process limit reached')
    monkeypatch.setattr('workspace.chat.subprocess.Popen', fail)
    with pytest.raises(OSError):
        attachment.start('Brief', detached=True)
    record = attachment.tasks.list()[0]
    assert record['status'] == 'failed'
    assert record['error'] == 'Process limit reached'


def test_reply_rechecks_closed_state_under_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from workspace.state import write_json
    store = WorkspaceStore(tmp_path)
    questions = Questions(store, store.create_run('Task', {})['id'])
    directory = questions.root / 'question-1'
    write_json(directory / 'request.json', {
        'id': 'question-1', 'kind': 'text', 'spec': {'prompt': 'Why?'}})
    original = questions._lock
    @contextmanager
    def close_before_acquire(directory):
        write_json(directory / 'closed.json', {'closed': True})
        with original(directory):
            yield
    monkeypatch.setattr(questions, '_lock', close_before_acquire)
    with pytest.raises(ValueError, match='closed'):
        questions.reply('question-1', 'Late answer')
    assert not (directory / 'reply.json').exists()


def test_detached_supervisor_sigterm_stops_worker(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    from workspace.experiment import ExecutionProfile, Strategy, snapshot
    from workspace.state import read_json
    runner = TaskRunner(WorkspaceStore(tmp_path / 'records'))
    task = runner.create('Brief', resolve_project(tmp_path), snapshot(
        Strategy(), ExecutionProfile(timeout=30)))
    script = '''
import sys
from workspace.task_runner import TaskRunner
original = TaskRunner.__init__
def initialize(self, store):
    original(self, store, worker_command=[sys.executable, '-c', 'import time; time.sleep(30)'])
TaskRunner.__init__ = initialize
from workspace.task_supervisor import main
main()
'''
    process = subprocess.Popen([sys.executable, '-c', script, str(runner.store.root), task['id']])
    worker_pid = None
    try:
        until = time.monotonic() + 5
        while time.monotonic() < until:
            record = runner.tasks.read(task['id'])
            if record['attempts']:
                run = read_json(runner.store.run_dir(record['attempts'][-1]['run_id']) / 'run.json')
                if run.get('supervisor_pid'):
                    worker_pid = run['pid']
                    break
            time.sleep(.02)
        assert worker_pid is not None
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=8) == 0
        assert runner.tasks.read(task['id'])['status'] == 'cancelled'
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
        with runner.tasks.lease(task['id']):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if worker_pid:
            try:
                os.killpg(worker_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_startup_validation_failure_is_durable(tmp_path):
    import shutil
    from workspace.experiment import ExecutionProfile, Strategy, snapshot
    project = tmp_path / 'project'
    project.mkdir()
    runner = TaskRunner(WorkspaceStore(tmp_path / 'records'))
    task = runner.create('Brief', resolve_project(project), snapshot(Strategy(), ExecutionProfile()))
    shutil.rmtree(project)
    with pytest.raises(ValueError, match='unavailable'):
        runner.run(task['id'])
    record = runner.tasks.read(task['id'])
    assert record['status'] == 'failed'
    assert 'unavailable' in record['error']


def test_custom_config_path_is_canonical_for_worker(tmp_path, monkeypatch):
    from config_manager import ConfigManager
    custom = tmp_path / 'custom.ini'
    custom.write_text('[AGENT]\ndefault_model = test-agent\n')
    monkeypatch.chdir(tmp_path)
    config = ConfigManager('custom.ini')
    assert config.config_file == str(custom)
    assert config.base_config.get('AGENT', 'default_model') == 'test-agent'
