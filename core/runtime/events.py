"""Session diagnostics routed into execution without a UI or output sink."""

from collections import deque
from contextlib import contextmanager


class EventChannel:
    """Bridge assembly diagnostics to execution with bounded preflight storage."""

    def __init__(self):
        self._pending = deque(maxlen=100)
        self._emit = None

    def emit(self, kind: str, data: dict) -> None:
        if self._emit is not None:
            self._emit(kind, data)
        else:
            self._pending.append((kind, dict(data)))

    @contextmanager
    def bind(self, emit):
        """Send diagnostics to a run, including those from assembly."""
        if self._emit is not None:
            raise RuntimeError('Session event channel already in use')
        self._emit = emit
        try:
            while self._pending:
                kind, data = self._pending.popleft()
                emit(kind, data)
            yield
        finally:
            self._emit = None
