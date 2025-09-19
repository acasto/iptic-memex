"""Chat input widget for the TUI footer."""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea


class ChatInput(TextArea):
    """Multiline-aware footer input with send shortcuts."""

    BINDINGS = [
        Binding("ctrl+k", "app.open_commands", "Commands", priority=True),
    ]

    class SendRequested(Message):
        """Message emitted when the user wants to send the input."""

        BUBBLE = True

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("id", "chat_input")
        super().__init__(*args, **kwargs)

    async def _on_key(self, event: events.Key) -> None:  # type: ignore[override]
        """Intercept key presses before Textual inserts characters."""

        if event.key == "shift+enter" or event.key == "ctrl+j":
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return

        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.SendRequested(self.text))
            return

        await super()._on_key(event)

    def clear_and_focus(self) -> None:
        self.text = ""
        try:
            self.focus()
        except Exception:
            pass

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, new_value: str) -> None:
        self.text = new_value
        self._move_cursor_to_end()

    @property
    def cursor_position(self) -> int:
        return len(self.text)

    @cursor_position.setter
    def cursor_position(self, _pos: int) -> None:
        self._move_cursor_to_end()

    def _move_cursor_to_end(self) -> None:
        lines = self.text.splitlines() or [""]
        row = len(lines) - 1
        col = len(lines[-1])
        try:
            self.cursor_location = (row, col)
        except Exception:
            pass
