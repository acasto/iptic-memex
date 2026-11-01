"""Terminal presentation of completion events; no execution decisions."""


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
        elif event.type in ('warning', 'status') or (event.type == 'debug' and self.verbose):
            message = event.data.get('message')
            if message:
                self.diagnostic(message)
        elif event.type == 'run_finished' and self._streamed:
            self.write('', end='\n')
