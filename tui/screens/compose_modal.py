"""Modal for composing multiline chat messages."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea


class ComposeModal(ModalScreen[Optional[str]]):
    """Simple modal that captures a multiline message."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+enter", "submit", "Send", priority=True),
    ]

    def __init__(self, prefill: str = "") -> None:
        super().__init__()
        self._prefill = prefill

    def compose(self) -> ComposeResult:
        with Vertical(id="compose_modal"):
            yield Static("Compose message (Ctrl+Enter to send, Esc to cancel)", id="compose_title")
            self.editor = TextArea(self._prefill or "", soft_wrap=True, id="compose_editor")
            yield self.editor

    async def on_mount(self) -> None:
        try:
            self.set_focus(self.editor)
            # Move cursor to end of prefilled text
            if self._prefill:
                lines = self._prefill.splitlines()
                if lines:
                    row = max(0, len(lines) - 1)
                    col = len(lines[-1])
                else:
                    row = 0
                    col = len(self._prefill)
                self.editor.cursor_location = (row, col)
        except Exception:
            pass

    def action_submit(self) -> None:
        self.dismiss(self.editor.text)

    def action_cancel(self) -> None:
        self.dismiss(None)
