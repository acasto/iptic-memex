"""Agent output policy as a consumer of ordinary runtime events."""

from ui.completion_renderer import CompletionRenderer


class AgentRenderer(CompletionRenderer):
    def __init__(self, write, diagnostic, *, output='final', verbose=False):
        super().__init__(write, diagnostic, verbose=verbose)
        self.output = output
        self._call_streamed = False

    def __call__(self, event):
        if event.type == 'provider_started':
            self._call_streamed = False
        if event.type == 'assistant_delta':
            self._call_streamed = True
        if self.output == 'full':
            if event.type == 'assistant_message' and not self._call_streamed:
                self.write(event.data['text'], flush=True)
            super().__call__(event)
        elif event.type in ('warning', 'error') or (event.type == 'debug' and self.verbose):
            if event.data.get('message'):
                self.diagnostic(event.data['message'])
