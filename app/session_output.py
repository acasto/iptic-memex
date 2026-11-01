"""Translate existing diagnostic helper output into execution events.

This adapter is an application service for providers/loaders still using
utils.output. It does not render, capture stdout, or provide a fake UI.
"""

from contextlib import contextmanager


class EventOutput:
    def __init__(self, channel):
        self.channel = channel

    def write(self, message='', **kwargs):
        if message:
            self.channel.emit('status', {'message': str(message)})

    def warning(self, message, **kwargs):
        self.channel.emit('warning', {'message': str(message)})

    def error(self, message, **kwargs):
        self.channel.emit('error', {'message': str(message)})

    def debug(self, message, **kwargs):
        self.channel.emit('debug', {'message': str(message)})

    status = write
    info = write
    success = write

    @staticmethod
    def style_text(text, *args, **kwargs):
        return str(text)

    @contextmanager
    def spinner(self, message='', **kwargs):
        if message:
            self.write(message)
        yield

    def stop_spinner(self):
        pass
