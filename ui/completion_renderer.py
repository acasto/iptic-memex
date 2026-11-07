"""Terminal task presentation shared by completion and CLI chat."""

from utils.tool_args import get_str


class CompletionRenderer:
    def __init__(self, write, diagnostic, *, verbose=False):
        self.write = write
        self.diagnostic = diagnostic
        self.verbose = verbose
        self._streamed = False

    def __call__(self, event):
        if event.type == 'assistant_delta':
            self.write(event.data['text'], end='', flush=True)
            self._streamed = True
        elif event.type == 'assistant_message' and event.data.get('intermediate') and self._streamed:
            self.write('', end='\n', flush=True)
            self._streamed = False
        elif event.type == 'tool_started':
            name = event.data['name']
            args = event.data.get('args') or {}
            desc = get_str(args, 'desc') or ''
            if not desc and name == 'cmd':
                command = get_str(args, 'command')
                arguments = get_str(args, 'arguments') or ''
                desc = (f'{command} {arguments}'.strip() if command
                        else get_str(event.data, 'content') or '')
            if len(desc) > 120:
                desc = desc[:117] + '...'
            self.diagnostic(f"Tool calling: {name}" + (f" — {desc}" if desc else ''))
        elif event.type == 'tool_finished' and event.data.get('status') == 'failed':
            self.diagnostic((event.data.get('result') or {}).get('error') or 'Tool failed')
        elif event.type == 'context' and event.data.get('message'):
            self.diagnostic(event.data['message'])
        elif event.type in ('warning', 'status') or (event.type == 'debug' and self.verbose):
            message = event.data.get('message')
            if message:
                self.diagnostic(message)
        elif event.type == 'run_finished' and self._streamed:
            self.write('', end='\n', flush=True)
