"""Filesystem policy for a task's project and host-owned control records."""

from pathlib import Path


def task_roots(project_root, store):
    """Prepare control directories and expose the project with read-only records.

    Workspace actions still publish through their explicit host-owned capability.
    A store outside the project is not granted as an extra filesystem root.
    """
    root = Path(project_root).resolve()
    policies = {str(root): 'rw'}
    for name in ('tasks', 'runs', 'notes', 'schedules'):
        path = (store.root / name).resolve()
        if not path.is_relative_to(store.root):
            raise ValueError('Workspace control directory escapes workspace')
        # Docker omits nonexistent bind roots. Create them before any worker can
        # mount the writable project, including schedules not registered yet.
        path.mkdir(parents=True, exist_ok=True)
        if path.is_relative_to(root):
            policies[str(path)] = 'ro'
    return tuple(policies.items())
