"""In-process ownership of retained sessions and bounded task execution."""

from collections import deque
from copy import deepcopy
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Lock, Thread, current_thread
from uuid import uuid4

from core.cancellation import CancellationToken
from core.runtime import InteractionService, execute


@dataclass(frozen=True)
class QueuedInput:
    id: str
    text: str
    source: str


class RunHandle:
    """Observe, cancel, or answer one execution without owning its session."""

    def __init__(self, session_id, parent_run_id, interactions):
        self.id = uuid4().hex
        self.session_id = session_id
        self.parent_run_id = parent_run_id
        self.cancellation = CancellationToken()
        self.interactions = interactions
        self._future = Future()
        self._lock = Lock()
        self._run_id = None
        self._pending = None
        self._state = 'starting'
        self._thread = None

    def snapshot(self):
        """Return cheap live status; no model calls or transcript mutation."""
        with self._lock:
            return dict(id=self.id, session_id=self.session_id, run_id=self._run_id,
                        parent_run_id=self.parent_run_id, state=self._state,
                        pending_input=deepcopy(self._pending))

    def result(self, timeout=None):
        """Wait for the outcome; a wait timeout does not cancel execution."""
        return self._future.result(timeout)

    def cancel(self, reason='Cancelled by caller'):
        self.cancellation.cancel(reason)

    def respond(self, request_id, value):
        self.interactions.respond(request_id, value)

    def _observe(self, event):
        with self._lock:
            self._run_id = event.run_id
            if event.type == 'run_started':
                self._state = 'running'
            elif event.type == 'interaction_requested':
                self._pending = dict(event.data)
                self._state = 'waiting_for_input'
            elif event.type == 'interaction_resolved':
                self._pending = None
                self._state = 'running'


class SessionHost:
    """Own an assembled session until close; run tasks serially on that session.

    This is a live application service, not a scheduler or durable continuation.
    Callers transfer exclusive use of the session while it is hosted. Application
    policy is expressed by RunRequest and explicit lifecycle callbacks; this class
    does not implement an agent strategy or another provider/tool loop.
    """

    def __init__(self, session, *, owns_session=True, inbox_limit=128):
        if inbox_limit < 1:
            raise ValueError('inbox_limit must be positive')
        if getattr(session, '_closed', False):
            raise ValueError('Cannot host a closed session')
        # Claim ownership under the runtime's existing per-session lock.
        if not session._execution_lock.acquire(blocking=False):
            raise RuntimeError('Session has an active run or host')
        try:
            if getattr(session, '_session_host', None) is not None:
                raise RuntimeError('Session already has a host')
            session._session_host = self
        finally:
            session._execution_lock.release()
        from app.runtime_services import configure_runtime
        try:
            configure_runtime(session)
        except BaseException:
            session._session_host = None
            raise
        self.session = session
        self.owns_session = owns_session
        self._lock = Lock()
        self._active = None
        self._closed = False
        self._inbox = deque()
        self._inbox_limit = inbox_limit

    def enqueue(self, text, *, source='caller'):
        """Queue selected text for a future call boundary; return its identity."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Input must be nonempty text')
        if len(text) > 32000:
            raise ValueError('Queued input exceeds 32000 characters')
        item = QueuedInput(uuid4().hex, text, str(source))
        with self._lock:
            if self._closed:
                raise RuntimeError('Session host is closed')
            if len(self._inbox) >= self._inbox_limit:
                raise ValueError('Session inbox is full')
            self._inbox.append(item)
        return item.id

    def pending(self):
        """Inspect inputs not yet attached, including inputs left after a run."""
        with self._lock:
            return tuple(self._inbox)

    def _deliver(self, session, index):
        # The mandatory accounting boundary runs before inbox consumption.
        boundary = getattr(session, 'before_provider_call', None)
        if boundary:
            boundary(session, index)
        with self._lock:
            batch = tuple(self._inbox)
        for item in batch:
            context = session.add_context('assistant', {
                'name': f'input:{item.id}', 'content': item.text})
            if context is None:
                raise ValueError(f'Could not attach queued input {item.id}')
            with self._lock:
                self._inbox.popleft()
            session.runtime_events.emit('input_attached', {
                'id': item.id, 'source': item.source, 'provider_call': index})

    def submit(self, request, *, on_event=None, interactions=None, parent_run_id=None,
               parent_cancellation=None, before_run=None, before_call=None, after_run=None):
        """Start one bounded task; reject overlapping submissions immediately."""
        handle = RunHandle(getattr(self.session, 'session_uid', None), parent_run_id,
                           interactions if interactions is not None else InteractionService())
        with self._lock:
            if self._closed:
                raise RuntimeError('Session host is closed')
            if self._active is not None:
                raise RuntimeError('Session already has an active run')
            self._active = handle
        if parent_cancellation is not None:
            parent_cancellation.register_cleanup(
                lambda: handle.cancel(parent_cancellation.reason() or 'Parent cancelled'))

        observer = on_event

        def observe(event):
            nonlocal observer
            handle._observe(event)
            if observer is not None:
                try:
                    observer(event)
                except Exception as exc:
                    observer = None
                    self.session.runtime_events.emit('warning', {
                        'code': 'observer_failed', 'message': str(exc)})

        def prepare(session, index):
            self._deliver(session, index)
            if before_call:
                before_call(session, index)

        def work():
            outcome = None
            error = None
            try:
                outcome = execute(self.session, request, on_event=observe,
                                  cancellation=handle.cancellation,
                                  interactions=handle.interactions, before_run=before_run,
                                  before_call=prepare, after_run=after_run)
            except BaseException as exc:
                error = exc
            finally:
                with handle._lock:
                    handle._state = outcome.status if outcome else 'failed'
                    handle._pending = None
                with self._lock:
                    self._active = None
                if error is not None:
                    handle._future.set_exception(error)
                else:
                    handle._future.set_result(outcome)

        thread = Thread(target=work, name=f'memex-run-{handle.id}', daemon=True)
        handle._thread = thread
        try:
            thread.start()
        except BaseException:
            with self._lock:
                self._active = None
            raise
        return handle

    def close(self, *, timeout=None):
        """Cancel active work and close only once it stops; timeout preserves safety.

        Cancellation is cooperative. On timeout the host remains closed to new
        submissions, but the session stays open; call close again after it stops.
        """
        with self._lock:
            self._closed = True
            active = self._active
        if active is not None:
            if active._thread is current_thread():
                raise RuntimeError('Close must be called outside the execution thread')
            active.cancel('Session host closed')
            try:
                active.result(timeout)
            except TimeoutError:
                raise
            except BaseException:
                pass  # The handle preserves failure; cleanup must still run.
        if self.owns_session:
            self.session.close()
        self.session._session_host = None

    def restore(self, checkpoint):
        """Durable provider/tool continuation is deliberately unsupported here."""
        raise NotImplementedError('SessionHost retains live state only; restart restore is unsupported')
