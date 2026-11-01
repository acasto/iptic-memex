"""Terminal task presentation shared by completion and CLI chat."""


class CompletionRenderer:
    def __init__(self, write, diagnostic, *, verbose=False):
        self.write = write
        self.diagnostic = diagnostic
        self.verbose = verbose
        self._streamed = False

    def __call__(self, event):
        if event.type == 'assistant_delta':
            self.write(event.data['text'], end='')
            self._streamed = True
        elif event.type == 'assistant_message' and event.data.get('intermediate') and self._streamed:
            self.write('', end='\n')
            self._streamed = False
        elif event.type == 'tool_started':
            self.diagnostic(f"Tool calling: {event.data['name']}")
        elif event.type == 'tool_finished' and event.data.get('status') == 'failed':
            self.diagnostic((event.data.get('result') or {}).get('error') or 'Tool failed')
        elif event.type == 'context' and event.data.get('message'):
            self.diagnostic(event.data['message'])
        elif event.type in ('warning', 'status') or (event.type == 'debug' and self.verbose):
            message = event.data.get('message')
            if message:
                self.diagnostic(message)
        elif event.type == 'run_finished' and self._streamed:
            self.write('', end='\n')
