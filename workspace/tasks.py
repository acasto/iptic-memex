"""Durable task identities and attempts, independent of model execution policy."""

from contextlib import contextmanager
from pathlib import Path
import uuid

from workspace.state import identifier, now, read_json, write_json


class TaskStore:
    """A task survives executions; an exclusive local lease owns each attempt."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.root = workspace.root / 'tasks'

    def directory(self, task_id):
        directory = self.root / identifier(task_id)
        if not directory.resolve().is_relative_to(self.workspace.root):
            raise ValueError('Task directory escapes workspace')
        return directory

    def bind_project(self, project_root):
        """Bind this notes/task store to one canonical project before execution."""
        import fcntl
        root = str(Path(project_root).resolve())
        if not self.root.resolve().is_relative_to(self.workspace.root):
            raise ValueError('Task directory escapes workspace')
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / 'project.lock').open('a+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            manifest = read_json(self.root / 'project.json')
            if manifest:
                if manifest.get('root') != root:
                    raise ValueError('Workspace belongs to another project; select a separate directory')
                return manifest
            # Existing research stores may be adopted only when their recorded
            # repositories agree. Never silently mix historical project evidence.
            for path in (self.workspace.root / 'runs').glob('*/run.json'):
                repository = read_json(path).get('repository')
                if repository and str(Path(repository).resolve()) != root:
                    raise ValueError('Workspace contains another project; select a separate directory')
            manifest = {'root': root, 'created_at': now(), 'version': 1}
            write_json(self.root / 'project.json', manifest)
            return manifest

    def create(self, brief, spec):
        if not isinstance(brief, str) or not brief.strip():
            raise ValueError('Task brief must be nonempty')
        task_id = 'task-' + uuid.uuid4().hex
        directory = self.directory(task_id)
        directory.mkdir(parents=True, exist_ok=False)
        record = dict(id=task_id, version=1, brief=brief, spec=spec, created_at=now(),
                      updated_at=now(), status='pending', attempts=[])
        write_json(directory / 'task.json', record)
        return record

    def read(self, task_id):
        record = read_json(self.directory(task_id) / 'task.json')
        if not record:
            raise ValueError(f'Unknown task: {task_id}')
        return record

    def save(self, record):
        record = dict(record, updated_at=now())
        write_json(self.directory(record['id']) / 'task.json', record)
        return record

    def list(self):
        if not self.root.exists():
            return []
        return [self.read(path.parent.name) for path in sorted(self.root.glob('*/task.json'))]

    @contextmanager
    def lease(self, task_id):
        """Prevent duplicate live attempts; worker inherits the lease descriptor."""
        import fcntl
        self.read(task_id)
        with (self.directory(task_id) / 'owner.lock').open('a+') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('Task already has a live execution owner') from exc
            # Closing this descriptor releases our reference; do not explicitly
            # unlock a lease still inherited by a worker after a supervisor crash.
            yield stream.fileno()
