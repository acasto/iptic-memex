"""Independent owner for explicitly detached chat tasks."""
import signal
import sys

from core.cancellation import CancellationToken
from workspace.state import WorkspaceStore
from workspace.task_runner import TaskRunner


def main():
    """Forward graceful supervisor termination through bounded worker cleanup."""
    token = CancellationToken()
    previous = signal.signal(signal.SIGTERM, lambda *_: token.cancel('Supervisor terminated'))
    try:
        TaskRunner(WorkspaceStore(sys.argv[1])).run(sys.argv[2], cancellation=token)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    main()
