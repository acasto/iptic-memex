"""One-off due-work dispatch; no resident loop or model-directed scheduling."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import time
import uuid

from core.cancellation import CancellationToken
from workspace.state import identifier, now, read_json, write_json
from workspace.task_runner import TaskRunner
from workspace.tasks import TaskBusy, TaskStore


class ScheduleBusy(ValueError):
    """Another invocation owns scheduler dispatch."""


class ScheduleDeferred(ValueError):
    """A schedule no longer meets dispatch conditions."""


def timestamp(value):
    """Require an explicit offset and normalize a timestamp to UTC."""
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Schedule time requires an explicit UTC offset or Z')
    return parsed.astimezone(timezone.utc)


def task_digest(task):
    data = json.dumps({'brief': task['brief'], 'spec': task['spec']},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


class ScheduleStore:
    """Host-owned one-off schedules, each with one stable occurrence record."""

    def __init__(self, workspace, project_root):
        from pathlib import Path
        self.workspace = workspace
        self.project_root = str(Path(project_root).resolve())
        self.tasks = TaskStore(workspace)
        self.root = workspace.root / 'schedules'
        if not self.root.resolve().is_relative_to(workspace.root):
            raise ValueError('Schedules directory escapes workspace')
        binding = read_json(self.tasks.root / 'project.json')
        if binding and binding.get('root') != self.project_root:
            raise ValueError('Workspace belongs to another project')

    def directory(self, schedule_id):
        path = self.root / identifier(schedule_id)
        if not path.resolve().is_relative_to(self.workspace.root):
            raise ValueError('Schedule directory escapes workspace')
        return path

    @contextmanager
    def lock(self, path, *, blocking=True):
        import fcntl
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a+') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:
                raise ScheduleBusy('Another run-due invocation is active') from exc
            yield

    def read(self, schedule_id):
        record = read_json(self.directory(schedule_id) / 'schedule.json')
        if not record:
            raise ValueError(f'Unknown schedule: {schedule_id}')
        if record.get('version') != 1 or record.get('kind') != 'once':
            raise ValueError('Unsupported schedule version or kind')
        try:
            if record['id'] != schedule_id or record['project_root'] != self.project_root:
                raise ValueError('Schedule identity or project does not match')
            identifier(record['task_id'])
            identifier(record['occurrence']['id'])
            timestamp(record['due_at'])
            if (type(record['enabled']) is not bool or not isinstance(record['task_digest'], str)
                    or record['status'] not in (
                        'scheduled', 'claimed', 'completed', 'needs_attention', 'needs_review')
                    or record['occurrence']['status'] not in (
                        'pending', 'claimed', 'finished', 'uncertain')):
                raise ValueError('Invalid schedule state')
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError('Malformed schedule record') from exc
        return record

    def save(self, record):
        write_json(self.directory(record['id']) / 'schedule.json', dict(record, updated_at=now()))

    def list(self):
        return [self.read(path.parent.name) for path in sorted(self.root.glob('*/schedule.json'))]

    def task(self, task_id):
        task = self.tasks.read(task_id)
        if task['spec']['project']['root'] != self.project_root:
            raise ValueError('Select the saved task project explicitly')
        # Limit the first release to workflows with proven unattended input semantics.
        if task['spec']['strategy']['workflow'] != 'session':
            raise ValueError('Scheduling currently supports directed/session tasks only')
        return task

    def add(self, task_id, at):
        due = timestamp(at).isoformat()
        task = self.task(task_id)
        self.tasks.bind_project(self.project_root)
        schedule_id = 'schedule-' + uuid.uuid4().hex
        directory = self.directory(schedule_id)
        directory.mkdir(parents=True, exist_ok=False)
        record = dict(id=schedule_id, version=1, kind='once', task_id=task_id,
                      project_root=self.project_root, task_digest=task_digest(task),
                      due_at=due, enabled=True, status='scheduled', created_at=now(),
                      occurrence={'id': 'occ-' + uuid.uuid4().hex, 'status': 'pending'})
        self.save(record)
        return self.read(schedule_id)

    def set_enabled(self, schedule_id, enabled):
        with self.lock(self.directory(schedule_id) / 'edit.lock'):
            record = self.read(schedule_id)
            if enabled and record['status'] != 'scheduled':
                raise ValueError('Only an unconsumed schedule can resume; inspect or create a new one')
            record['enabled'] = enabled
            self.save(record)
            return self.read(schedule_id)


class Scheduler:
    """Run eligible saved tasks once per invocation, using existing task supervision."""

    CLEANUP_SECONDS = 10

    def __init__(self, schedules, *, runner=None, clock=None, monotonic=None):
        self.schedules = schedules
        self.runner = runner or TaskRunner(schedules.workspace)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic

    def _finish(self, schedule_id, result=None, error=None):
        with self.schedules.lock(self.schedules.directory(schedule_id) / 'edit.lock'):
            record = self.schedules.read(schedule_id)
            if result:
                record['occurrence'].update(status='finished', run_id=result['run_id'],
                                            outcome=result['status'], finished_at=now())
                record['status'] = ('completed' if result['status'] == 'completed'
                                    else 'needs_attention')
                if result.get('error'):
                    record['occurrence']['error'] = result['error']
            else:
                record['status'] = 'needs_review'
                record['occurrence'].update(status='uncertain', error=error)
            record['enabled'] = False
            self.schedules.save(record)

    def _reconcile(self, record):
        """Only conclusive final artifacts allow reconciliation; never replay a claim."""
        task = self.schedules.tasks.inspect(record['task_id'])
        if task['status'] == 'running':
            return False
        matches = []
        for path in (self.schedules.workspace.root / 'runs').glob('*/run.json'):
            run = read_json(path)
            origin = run.get('execution_origin') or {}
            if (run.get('task_id') == record['task_id'] and
                    origin.get('occurrence_id') == record['occurrence']['id']):
                result = read_json(path.parent / 'task-result.json')
                if (result.get('task_id') == record['task_id'] and
                        result.get('run_id') == run.get('id') and
                        result.get('status') == run.get('status') and
                        run.get('phase') == 'finished'):
                    matches.append(result)
        if len(matches) == 1:
            self._finish(record['id'], matches[0])
        else:
            self._finish(record['id'], error='Claim has no unique conclusive result; inspect before retrying')
        return True

    def run_due(self, *, max_runs=1, max_seconds=600, dry_run=False, cancellation=None):
        if type(max_runs) is not int or max_runs < 1:
            raise ValueError('max_runs must be a positive integer')
        if (isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or
                not math.isfinite(max_seconds) or max_seconds <= 0):
            raise ValueError('max_seconds must be positive and finite')
        cutoff = self.clock()
        records = self.schedules.list()
        due = sorted((r for r in records if r['enabled'] and r['status'] == 'scheduled'
                      and timestamp(r['due_at']) <= cutoff), key=lambda r: (r['due_at'], r['id']))
        if dry_run:
            return {'status': 'dry_run', 'due': [r['id'] for r in due],
                    'unresolved': [r['id'] for r in records if r['occurrence']['status'] == 'claimed']}
        # No filesystem writes for a genuinely idle tick.
        if not due and not any(r['occurrence']['status'] == 'claimed' for r in records):
            return {'status': 'idle', 'runs': [], 'deferred': []}
        token = cancellation or CancellationToken()
        deadline = self.monotonic() + max_seconds
        try:
            with self.schedules.lock(self.schedules.root / 'dispatch.lock', blocking=False):
                return self._dispatch(cutoff, deadline, max_runs, token)
        except ScheduleBusy:
            return {'status': 'busy', 'runs': [], 'deferred': []}

    def _dispatch(self, cutoff, deadline, max_runs, token):
        runs, deferred = [], []
        reconciled = []
        for record in self.schedules.list():
            if record['occurrence']['status'] == 'claimed':
                if not self._reconcile(record):
                    return {'status': 'busy', 'runs': [], 'deferred': [], 'reconciled': reconciled}
                reconciled.append(record['id'])
        records = sorted(self.schedules.list(), key=lambda r: (r['due_at'], r['id']))
        for record in records:
            if token.is_cancelled() or len(runs) >= max_runs:
                break
            if not record['enabled'] or record['status'] != 'scheduled':
                continue
            if timestamp(record['due_at']) > cutoff:
                continue
            sid, task_id = record['id'], record['task_id']
            origin = {'schedule_id': sid, 'occurrence_id': record['occurrence']['id'],
                      'due_at': record['due_at'], 'task_digest': record['task_digest']}

            def claim():
                with self.schedules.lock(self.schedules.directory(sid) / 'edit.lock'):
                    current = self.schedules.read(sid)
                    if not current['enabled'] or current['status'] != 'scheduled':
                        raise ScheduleDeferred('Schedule paused or already consumed')
                    task = self.schedules.task(task_id)
                    if task_digest(task) != current['task_digest']:
                        raise ValueError('Saved task inputs changed; create a new schedule explicitly')
                    timeout = float(task['spec']['profile']['timeout'])
                    if (not math.isfinite(timeout) or timeout <= 0):
                        raise ValueError('Invalid saved task timeout')
                    if token.is_cancelled() or timeout + self.CLEANUP_SECONDS > deadline - self.monotonic():
                        raise ScheduleDeferred('Insufficient tick allowance or invocation cancelled')
                    current['occurrence'].update(status='claimed', claimed_at=now())
                    current['status'] = 'claimed'
                    self.schedules.save(current)

            try:
                result = self.runner.run(task_id, before_start=claim, execution_origin=origin,
                                         unattended=True, cancellation=token)
            except ScheduleDeferred as exc:
                deferred.append({'schedule_id': sid, 'reason': str(exc)})
                continue
            except TaskBusy as exc:
                deferred.append({'schedule_id': sid, 'reason': str(exc)})
                continue
            except Exception as exc:
                self._finish(sid, error=str(exc))
                runs.append({'schedule_id': sid, 'status': 'needs_review', 'error': str(exc)})
                continue
            self._finish(sid, result)
            if result['status'] == 'cancelled':
                token.cancel('Scheduled attempt cancelled')
            runs.append({'schedule_id': sid, 'task_id': task_id,
                         'run_id': result['run_id'], 'status': result['status']})
        attention = (any(r['status'] != 'completed' for r in runs) or
                     any(self.schedules.read(sid)['status'] != 'completed' for sid in reconciled))
        status = ('cancelled' if token.is_cancelled() else 'needs_attention' if attention else
                  'completed' if runs or reconciled else 'deferred' if deferred else 'idle')
        return {'status': status, 'runs': runs, 'deferred': deferred, 'reconciled': reconciled}
