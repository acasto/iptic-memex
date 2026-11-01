"""One input service for direct callers, live responders, and unattended runs."""

from pathlib import Path
from threading import Condition
from uuid import uuid4

from base_classes import InteractionNeeded
from core.runtime.contracts import InteractionRequest


class InteractionService:
    """Request validated input without knowing which interface can provide it.

    A direct handler returns responses synchronously. With wait=True, another
    thread responds by ID. Without either, the caller receives needs_input.
    Waiting is in-process only; no action stack is persisted for later replay.
    """

    def __init__(self, handler=None, *, wait=False):
        self.handler = handler
        self.wait = wait
        self._condition = Condition()
        self._pending = None
        self._response = None
        self._answered = False
        self._emit = lambda kind, data: None
        self._token = None
        self._bound = False

    def bind(self, emit, token):
        if self._bound:
            raise RuntimeError('Interaction service already belongs to an active run')
        self._bound = True
        self._emit, self._token = emit, token
        token.register_cleanup(self._wake)

    def unbind(self):
        self._bound = False
        self._emit = lambda kind, data: None
        self._token = None

    def _wake(self):
        with self._condition:
            self._condition.notify_all()

    @staticmethod
    def validate(request, value):
        """Enforce constraints at the service boundary, regardless of client."""
        spec = request.spec
        if request.kind == 'bool':
            if not isinstance(value, bool):
                raise ValueError('Expected a boolean response')
        elif request.kind == 'text':
            if not isinstance(value, str):
                raise ValueError('Expected a text response')
        elif request.kind == 'choice':
            values = value if spec.get('multi') else [value]
            if not isinstance(values, list) or any(v not in spec['options'] for v in values):
                raise ValueError('Response must use the offered choices')
        elif request.kind == 'files':
            if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                raise ValueError('Expected file paths')
            if not spec.get('multiple', True) and len(value) > 1:
                raise ValueError('Only one file may be selected')
            for path in value:
                if spec.get('must_exist', True) and not Path(path).expanduser().is_file():
                    raise ValueError(f'File not found: {path}')
                if spec.get('accept') and not path.lower().endswith(tuple(spec['accept'])):
                    raise ValueError(f'Unsupported file type: {path}')
        return value

    def respond(self, request_id, value):
        """Accept one response for the currently pending request."""
        with self._condition:
            if self._pending is None or self._pending.id != request_id or self._answered:
                raise ValueError('Unknown, stale, or already answered interaction')
            self._response = self.validate(self._pending, value)
            self._answered = True
            self._condition.notify_all()

    def prompt(self, need):
        """Resolve a legacy stepwise request through the same input service."""
        return self.request(need.kind, need.spec)

    def request(self, kind, spec):
        request = InteractionRequest(uuid4().hex, kind, dict(spec))
        with self._condition:
            if self._pending is not None:
                raise RuntimeError('An interaction is already pending')
            self._pending = request
            self._answered = False
        try:
            self._emit('interaction_requested', {'id': request.id, 'kind': kind, 'spec': dict(spec)})
            if self.handler:
                self.respond(request.id, self.handler(request))
            elif not self.wait and not self._answered:
                need = InteractionNeeded(kind, dict(spec), request.id)
                need.runtime_request_id = request.id
                raise need
            with self._condition:
                while not self._answered:
                    if self._token and self._token.is_cancelled():
                        raise InterruptedError('Run cancelled while waiting for input')
                    self._condition.wait()
                if self._token and self._token.is_cancelled():
                    raise InterruptedError('Run cancelled while waiting for input')
                value = self._response
            self._emit('interaction_resolved', {'id': request.id})
            return value
        finally:
            with self._condition:
                self._pending = None
                self._response = None
                self._answered = False

    def ask_bool(self, prompt, *, default=None):
        return self.request('bool', {'prompt': prompt, 'default': default})

    def ask_text(self, prompt, *, default=None, multiline=False):
        return self.request('text', {'prompt': prompt, 'default': default, 'multiline': multiline})

    def ask_choice(self, prompt, options, *, default=None, multi=False):
        return self.request('choice', {'prompt': prompt, 'options': options,
                                       'default': default, 'multi': multi})

    def ask_files(self, prompt, *, accept=None, multiple=True, must_exist=True):
        return self.request('files', {'prompt': prompt, 'accept': accept,
                                      'multiple': multiple, 'must_exist': must_exist})

    def emit(self, kind, data):
        self._emit(kind, data)
