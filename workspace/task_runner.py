"""Foreground task supervision with durable attempt records and hard deadlines."""

from dataclasses import asdict
import os
from pathlib import Path
import subprocess
import sys
import time

from workspace.engine import APP_ROOT, WorkspaceEngine, WorkspaceSettings
from workspace.experiment import ExecutionProfile, Strategy, validate_experiment
from workspace.state import now, read_json, write_json
from workspace.tasks import TaskStore


class TaskRunner:
    """Launch an attempt in a process group; records remain usable after exit."""

    def __init__(self, store, *, worker_command=None):
        self.store, self.tasks = store, TaskStore(store)
        self.worker_command = worker_command or [sys.executable, '-m', 'workspace.task_worker']

    def create(self, brief, project, experiment, *, config_file=None):
        strategy, profile = Strategy(**experiment['strategy']), ExecutionProfile(**experiment['profile'])
        validate_experiment(strategy, profile)
        if not isinstance(brief, str) or not brief.strip():
            raise ValueError('Task brief must be nonempty')
        self.tasks.bind_project(project.root)
        project_data = asdict(project)
        project_data['root'] = str(project.root)
        project_data['config_path'] = str(project.config_path) if project.config_path else None
        return self.tasks.create(brief, dict(experiment, project=project_data, config_file=config_file))

    def run(self, task_id, *, on_started=None, cancellation=None):
        """Persist failures only while holding this task's exclusive ownership lease."""
        with self.tasks.lease(task_id) as lease_fd:
            try:
                return self._run_owned(task_id, lease_fd, on_started, cancellation)
            except Exception as exc:
                task = self.tasks.read(task_id)
                task.update(status='failed', error=str(exc))
                if task['attempts'] and task['attempts'][-1]['status'] == 'running':
                    attempt = task['attempts'][-1]
                    attempt.update(status='failed', error=str(exc), finished_at=now())
                    self.store.update_run(attempt['run_id'], status='failed', phase='finished',
                                          error=str(exc), finished_at=now())
                self.tasks.save(task)
                raise

    def _run_owned(self, task_id, lease_fd, on_started, cancellation):
        task = self.tasks.read(task_id)
        if task['attempts']:
            (self.tasks.directory(task_id) / 'cancel').unlink(missing_ok=True)
        spec = dict(task['spec'])
        self.tasks.bind_project(spec['project']['root'])
        strategy, profile = Strategy(**spec['strategy']), ExecutionProfile(**spec['profile'])
        validate_experiment(strategy, profile)
        if not Path(spec['project']['root']).is_dir():
            raise ValueError('Task project directory is unavailable')
        # An explicit repeat owns the lease and starts a NEW attempt; it never
        # resumes a Python stack or silently retries external side effects.
        if task['status'] == 'running' and task['attempts']:
            prior = task['attempts'][-1]
            prior.update(status='interrupted', error='Previous owner exited without final status')
            self.store.update_run(prior['run_id'], status='interrupted', phase='finished')
        record = self.store.create_run(task['brief'], dict(
            kind='task', task_id=task_id, repository=spec['project']['root'],
            strategy=spec['strategy'], profile=spec['profile'],
            run_timeout_seconds=profile.timeout, synthesis_timeout_seconds=0))
        run_id = record['id']
        task['attempts'].append({'run_id': run_id, 'status': 'running', 'started_at': now()})
        task.update(status='running', owner_pid=os.getpid())
        self.tasks.save(task)
        spec.update(task_id=task_id, run_id=run_id, directory=str(self.store.root),
                    brief=task['brief'], deadline_monotonic=time.monotonic() + profile.timeout)
        write_json(self.store.run_dir(run_id) / 'experiment.json', spec)
        cleanup = WorkspaceEngine(self.store, WorkspaceSettings(repository=spec['project']['root']))
        process, result, reason = None, {}, None
        log = (self.store.run_dir(run_id) / 'process.log').open('wb')
        try:
            if on_started:
                on_started({'task_id': task_id, 'run_id': run_id,
                            'directory': str(self.store.run_dir(run_id))})
            if ((cancellation and cancellation.is_cancelled()) or
                    (self.tasks.directory(task_id) / 'cancel').exists()):
                raise InterruptedError('Cancelled before worker startup')
            # File-backed input cannot block the supervisor on a full stdin pipe.
            with (self.store.run_dir(run_id) / 'experiment.json').open('rb') as input_file:
                process = subprocess.Popen(
                    self.worker_command, cwd=APP_ROOT, stdin=input_file, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(lease_fd,))
            cleanup._children['task'] = (process, log)
            self.store.update_run(run_id, pid=process.pid, supervisor_pid=os.getpid())
            task['owner_pid'] = process.pid
            self.tasks.save(task)
            while process.poll() is None:
                if ((cancellation and cancellation.is_cancelled()) or
                        (self.tasks.directory(task_id) / 'cancel').exists()):
                    reason = 'cancelled'
                    break
                if time.monotonic() >= spec['deadline_monotonic']:
                    reason = 'timed_out'
                    break
                time.sleep(0.05)
        except (KeyboardInterrupt, InterruptedError):
            reason = 'cancelled'
        except Exception as exc:
            reason = 'failed'
            result['error'] = str(exc)
        finally:
            if process is not None:
                cleanup._stop(['task'])
            log.close()
        saved_result = read_json(self.store.run_dir(run_id) / 'task-result.json')
        result = dict(saved_result, **result)
        status = reason or result.get('status') or 'failed'
        if reason is None and process is not None and process.returncode:
            status = 'failed'
            result['error'] = f'Task process exited with code {process.returncode}'
        result.update(status=status, task_id=task_id, run_id=run_id)
        result.setdefault('answer', '')
        if status == 'failed' and not result.get('error'):
            result['error'] = ('Task execution failed; inspect saved agent outcomes' if saved_result
                               else 'Task process ended without a result; inspect process.log')
        state = read_json(self.store.run_dir(run_id) / 'task-state.json')
        if state:
            for agent_id, node in state.get('agents', {}).items():
                if node.get('status') in ('starting', 'running'):
                    terminal = status if status in ('cancelled', 'timed_out', 'failed') else 'interrupted'
                    node.update(status=terminal, error='Execution owner exited')
                    self.store.update_worker(run_id, agent_id, status=terminal, phase='finished',
                                             error='Execution owner exited')
            write_json(self.store.run_dir(run_id) / 'task-state.json', state)
            if 'agents' in result:
                for agent in result['agents']:
                    if agent['id'] in state.get('agents', {}):
                        agent['status'] = state['agents'][agent['id']]['status']
        self.store.update_run(run_id, status=status, phase='finished', finished_at=now(),
                              error=result.get('error'))
        task['attempts'][-1].update(status=status, finished_at=now(), error=result.get('error'))
        task.update(status=status)
        self.tasks.save(task)
        write_json(self.store.run_dir(run_id) / 'task-result.json', result)
        return result
