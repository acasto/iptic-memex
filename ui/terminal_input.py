"""Synchronous terminal input ownership while CLI chat is executing a task."""

from contextlib import contextmanager
import sys


class TerminalInputGuard:
    """Suppress terminal echo/typeahead during a run, preserving signal handling.

    Piped input is untouched. On terminals supporting termios, typing is accepted
    only at a user/input-request prompt. Ctrl-C still interrupts execution.
    """

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stdin
        self._fd = None
        self._original = None
        self._termios = None

    def __enter__(self):
        try:
            import termios
        except ImportError:
            return self
        self._termios = termios
        try:
            if self.stream.isatty():
                self._fd = self.stream.fileno()
                self._original = termios.tcgetattr(self._fd)
                self._quiet()
        except (AttributeError, OSError, ValueError, termios.error):
            self._fd = None
            self._original = None
        return self

    def _quiet(self):
        if self._original is not None:
            try:
                attrs = self._termios.tcgetattr(self._fd)
                attrs[3] &= ~(self._termios.ECHO | self._termios.ECHONL)
                self._termios.tcsetattr(self._fd, self._termios.TCSANOW, attrs)
            except (OSError, self._termios.error):
                pass  # A disconnected terminal must not prevent cleanup.

    def _restore(self):
        if self._original is not None:
            # Discard keystrokes entered while busy before showing a new prompt.
            try:
                try:
                    self._termios.tcflush(self._fd, self._termios.TCIFLUSH)
                finally:
                    self._termios.tcsetattr(self._fd, self._termios.TCSANOW, self._original)
            except (OSError, self._termios.error):
                pass

    @contextmanager
    def allow_input(self):
        """Temporarily hand input back to an actual confirmation/input prompt."""
        self._restore()
        try:
            yield
        finally:
            self._quiet()

    def __exit__(self, *exc):
        try:
            self._restore()
        finally:
            self._original = None
