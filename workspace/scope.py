"""Filesystem policy for a task's project and host-owned control records."""

from pathlib import Path


def task_roots(project_root, store):
    """Expose the project while protecting task/run/note records from file tools.

    Workspace actions still publish through their explicit host-owned capability.
    A store outside the project is not granted as an extra filesystem root.
    """
    root = Path(project_root).resolve()
    policies = {str(root): 'rw'}
    for name in ('tasks', 'runs', 'notes'):
        path = (store.root / name).resolve()
        if path.is_relative_to(root):
            policies[str(path)] = 'ro'
    return tuple(policies.items())
