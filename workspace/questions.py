"""Attempt-scoped input exchange; no suspended Python state is replayed."""
from contextlib import contextmanager
from dataclasses import asdict
import time

from core.runtime.contracts import InteractionRequest
from core.runtime.interaction import InteractionService
from workspace.state import identifier, read_json, write_json


class Questions:
    def __init__(self, store, run_id):
        self.root = store.run_dir(run_id) / 'questions'

    @contextmanager
    def _lock(self, directory):
        import fcntl
        with (directory / 'reply.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def pending(self):
        return [read_json(path) for path in sorted(self.root.glob('*/request.json'))
                if not (path.parent / 'closed.json').exists()
                and not (path.parent / 'reply.json').exists()]

    def reply(self, request_id, value):
        directory = self.root / identifier(request_id)
        request = read_json(directory / 'request.json')
        if not request or (directory / 'closed.json').exists():
            raise ValueError('Unknown or closed question')
        with self._lock(directory):
            if (directory / 'closed.json').exists():
                raise ValueError('Unknown or closed question')
            if (directory / 'reply.json').exists():
                raise ValueError('Question already answered')
            InteractionService.validate(InteractionRequest(**request), value)
            write_json(directory / 'reply.json', {'value': value})

    def handler(self, cancellation, deadline):
        def ask(request):
            directory = self.root / identifier(request.id)
            write_json(directory / 'request.json', asdict(request))
            try:
                while time.monotonic() < deadline and not cancellation.is_cancelled():
                    reply = read_json(directory / 'reply.json')
                    if reply:
                        return InteractionService.validate(request, reply['value'])
                    time.sleep(0.05)
                raise InterruptedError('Task stopped while waiting for input')
            finally:
                with self._lock(directory):
                    write_json(directory / 'closed.json', {'closed': True})
        return ask
