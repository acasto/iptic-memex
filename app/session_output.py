"""Translate existing diagnostic helper output into execution events.

This adapter is an application service for providers/loaders still using
utils.output. It does not render, capture stdout, or provide a fake UI.
"""

from contextlib import contextmanager


class EventOutput:
    def __init__(self, channel, idle_output=None):
        self.channel = channel
        self.idle_output = idle_output

    def _send(self, kind, message, kwargs):
        if self.idle_output is not None and not self.channel.active:
            method = getattr(self.idle_output, kind if kind != 'status' else 'write')
            return method(message, **kwargs)
        if message:
            self.channel.emit(kind, {'message': str(message)})

    def write(self, message='', **kwargs):
        return self._send('status', message, kwargs)

    def warning(self, message, **kwargs):
        return self._send('warning', message, kwargs)

    def error(self, message, **kwargs):
        return self._send('error', message, kwargs)

    def debug(self, message, **kwargs):
        return self._send('debug', message, kwargs)

    status = write
    info = write
    success = write

    def style_text(self, text, *args, **kwargs):
        if self.idle_output is not None and not self.channel.active:
            return self.idle_output.style_text(text, *args, **kwargs)
        return str(text)

    @contextmanager
    def spinner(self, message='', **kwargs):
        if message:
            self.write(message)
        yield

    def stop_spinner(self):
        if self.idle_output is not None and not self.channel.active:
            self.idle_output.stop_spinner()

    @contextmanager
    def tool_scope(self, name, **kwargs):
        yield

    command_scope = tool_scope

    @contextmanager
    def suppress_below(self, *args):
        yield
