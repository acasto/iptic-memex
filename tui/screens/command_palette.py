"""Command palette modal for the Textual TUI."""

from __future__ import annotations

from typing import List, Optional

from rich.text import Text

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static

from tui.models import CommandItem


class CommandListItem(ListItem):
    """List item storing command metadata."""

    def __init__(self, command: CommandItem) -> None:
        super().__init__(Static(f"{command.title}\n[dim]{command.help}[/dim]", markup=True))
        self.command = command


class CommandPalette(ModalScreen[Optional[CommandItem]]):
    """Modal palette that lets the user search and pick commands."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, commands: List[CommandItem]) -> None:
        super().__init__()
        self._all = list(commands)
        self._filtered = list(commands)

    def compose(self) -> ComposeResult:
        with Vertical(id="command_palette"):
            self.search = Input(placeholder="Search commands…", id="command_palette_search")
            yield self.search
            self.list_view = ListView(id="command_palette_list")
            yield self.list_view
            yield Static(Text("Enter to select · Esc to cancel", style="dim"))

    async def on_mount(self) -> None:
        self._refresh_list()
        self.set_focus(self.search)

    def _refresh_list(self) -> None:
        self.list_view.clear()
        for item in self._filtered:
            self.list_view.append(CommandListItem(item))
        if self._filtered:
            try:
                self.list_view.index = 0
            except Exception:
                pass

    def on_input_changed(self, event: Input.Changed) -> None:  # type: ignore[override]
        query = (event.value or "").strip().lower()
        if not query:
            self._filtered = list(self._all)
        else:
            self._filtered = [cmd for cmd in self._all if query in cmd.title.lower() or query in cmd.help.lower()]
        self._refresh_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:  # type: ignore[override]
        if self._filtered:
            self.dismiss(self._filtered[0])

    def on_list_view_selected(self, event: ListView.Selected) -> None:  # type: ignore[override]
        command = getattr(event.item, "command", None)
        self.dismiss(command)

    def action_close(self) -> None:
        self.dismiss(None)

