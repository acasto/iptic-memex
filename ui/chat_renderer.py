"""CLI chat presentation for waiting, streamed replies, and final messages."""

from ui.completion_renderer import CompletionRenderer


class ChatRenderer(CompletionRenderer):
    """Keep progress and labels out of the execution runtime."""

    def __init__(self, output, label, *, stream):
        super().__init__(output.write, output.warning)
        self.output = output
        self.label = label
        self.stream = stream
        self._waiting = None
        self._label_printed = False

    def _start_wait(self):
        if self._waiting is not None:
            return
        factory = getattr(self.output, 'spinner', None)
        self._waiting = factory('Waiting for response...') if factory else False
        if self._waiting:
            self._waiting.__enter__()
        if not self._waiting or not getattr(self._waiting, 'enabled', False):
            self.write('Waiting for response...', flush=True)

    def _stop_wait(self):
        if self._waiting:
            self._waiting.__exit__(None, None, None)
        self._waiting = None

    def _show_label(self):
        if not self._label_printed:
            self.write(f'{self.label} ', end='', flush=True)
            self._label_printed = True

    def __call__(self, event):
        kind = event.type
        if kind in ('run_started', 'provider_started', 'interaction_resolved'):
            self._start_wait()
        elif kind == 'assistant_delta':
            self._stop_wait()
            self._show_label()
        elif kind == 'assistant_message':
            self._stop_wait()
            if not self.stream and not event.data.get('intermediate') and event.data.get('text'):
                self._show_label()
                self.write(event.data['text'], flush=True)
            if event.data.get('intermediate'):
                self._label_printed = False
        elif kind in ('provider_finished', 'tool_started', 'interaction_requested', 'run_finished',
                      'warning', 'status', 'error') or (kind == 'context' and event.data.get('message')):
            self._stop_wait()
            if kind == 'interaction_requested' and self._streamed:
                self.write('', end='\n', flush=True)
                self._streamed = False
                self._label_printed = False
        super().__call__(event)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._stop_wait()
