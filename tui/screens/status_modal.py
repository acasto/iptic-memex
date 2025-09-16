"""Modal overlay to show status logs in a larger view."""

from __future__ import annotations

from typing import Iterable, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from tui.widgets.status_panel import StatusPanel


class StatusModal(ModalScreen[None]):
    """Full-width status viewer modal."""

    BINDINGS = [Binding("escape", "close", "Close", show=True)]

    def __init__(self, history: Iterable[Tuple[str, str]] | None = None) -> None:
        super().__init__()
        self._history = list(history or [])

    def compose(self) -> ComposeResult:
        with Vertical(id="status_modal"):
            yield Static("Status", id="status_title")
            self.panel = StatusPanel(id="status_full", markup=True)
            yield self.panel

    async def on_mount(self) -> None:
        self._load_history()

    def _load_history(self) -> None:
        for text, level in self._history:
            self.panel.log_status(text, level)

    def action_close(self) -> None:
        self.dismiss(None)

