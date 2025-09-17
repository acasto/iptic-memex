"""Chat input widget for the TUI footer."""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea


class ChatInput(TextArea):
    """Multiline-aware footer input with send shortcuts."""

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

        key = event.key.lower()
        aliases = {alias.lower() for alias in getattr(event, "aliases", [])}
        ctrl = bool(getattr(event, "ctrl", False))
        shift = bool(getattr(event, "shift", False))
        base = key.split('+')[-1]

        # Enter submits when no modifiers
        if base in {"enter", "return"} and not ctrl and not shift:
            event.stop(); event.prevent_default()
            self.post_message(self.SendRequested(self.text))
            return

        # Ctrl+J inserts newline
        if (base == "j" and ctrl) or "ctrl+j" in aliases:
            event.stop(); event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return

        # Shift+Enter inserts newline
        if (base in {"enter", "return"} and shift) or "shift+enter" in aliases:
            event.stop(); event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return

        await super()._on_key(event)

    async def handle_key(self, event: events.Key) -> bool:  # type: ignore[override]
        key = event.key.lower()
        aliases = {alias.lower() for alias in getattr(event, "aliases", [])}
        ctrl = bool(getattr(event, "ctrl", False))
        shift = bool(getattr(event, "shift", False))
        base = key.split('+')[-1]

        if base in {"enter", "return"} and not ctrl and not shift:
            event.prevent_default()
            event.stop()
            self.post_message(self.SendRequested(self.text))
            return True
        if (base == "j" and ctrl) or "ctrl+j" in aliases:
            event.prevent_default()
            event.stop()
            self.insert_text("\n")
            return True
        if (base in {"enter", "return"} and shift) or "shift+enter" in aliases:
            event.prevent_default()
            event.stop()
            self.insert_text("\n")
            return True
        return await super().handle_key(event)

    def check_consume_key(self, key: str, character: str | None = None) -> bool:  # type: ignore[override]
        if key.lower() in {"enter", "return"}:
            return True
        return super().check_consume_key(key, character)

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
