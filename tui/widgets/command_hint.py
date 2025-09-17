"""Inline command hint widget for the TUI input."""

from __future__ import annotations

from typing import Iterable, List, Optional

from rich.console import Group
from rich.columns import Columns
from rich.panel import Panel
from rich.text import Text

from textual.widgets import Static

from tui.models import CommandItem


class CommandHint(Static):
    """Display a short list of matching commands beneath the input box."""

    DEFAULT_MAX: Optional[int] = None  # None = show all

    def __init__(self, *args, max_items: Optional[int] = DEFAULT_MAX, **kwargs) -> None:
        super().__init__(*args, markup=True, **kwargs)
        self.max_items = max_items
        self.styles.visibility = "hidden"

    def update_suggestions(self, commands: Iterable[CommandItem], *, prefix: Optional[str] = None) -> None:
        """Render the provided command suggestions."""

        prefix = (prefix or '').strip().lower()
        items: List[CommandItem] = list(commands)
        if isinstance(self.max_items, int) and self.max_items > 0:
            items = items[: self.max_items]
        if not items:
            self.styles.visibility = "hidden"
            self.update('')
            return

        rendered: List[Text] = []
        for item in items:
            if prefix:
                highlight_text = self._highlight_prefix(item.title, prefix)
            else:
                highlight_text = Text(item.title, style='bold')
            help_text = (item.help or '').strip()
            if help_text:
                line = Text.assemble(highlight_text, Text(f" — {help_text}", style='dim'))
            else:
                line = highlight_text
            rendered.append(line)

        columns = Columns(rendered, equal=True, expand=True)

        # Derive a helpful title: show either Commands or Subcommands for /<cmd>
        title = 'Commands'
        try:
            # If all entries share same command and at least one has a sub, treat as subcommands
            cmds = {tuple(getattr(it, 'path', ['','']))[0] for it in items}
            subs = {tuple(getattr(it, 'path', ['','']))[1] for it in items}
            if len(cmds) == 1 and any(s for s in subs):
                cmd = list(cmds)[0]
                title = f"Subcommands for /{cmd}"
        except Exception:
            pass

        panel = Panel(columns, title=title, border_style='cyan', padding=(0, 1), expand=True)
        self.update(panel)
        self.styles.visibility = "visible"

    def show_message(self, message: str) -> None:
        """Display an informational message in place of command suggestions."""

        text = (message or '').strip()
        if not text:
            self.styles.visibility = "hidden"
            self.update('')
            return
        body = Text(text, style='bold')
        panel = Panel(body, title='Info', border_style='cyan', padding=(0, 1), expand=True)
        self.update(panel)
        self.styles.visibility = "visible"

    def show_strings(self, values: Iterable[str], *, title: str = 'Suggestions') -> None:
        """Render a simple list of string suggestions."""

        entries = [str(v) for v in values if str(v).strip()]
        if not entries:
            self.styles.visibility = "hidden"
            self.update('')
            return
        texts = [Text(entry, style='bold') for entry in entries]
        columns = Columns(texts, equal=True, expand=True)
        panel = Panel(columns, title=title, border_style='cyan', padding=(0, 1), expand=True)
        self.update(panel)
        self.styles.visibility = "visible"

    def _highlight_prefix(self, title: str, prefix: str) -> Text:
        text = Text(title, style='bold')
        if not prefix:
            return text
        lower_title = title.lower()
        pos = lower_title.find(prefix)
        if pos < 0:
            return text
        before = Text(title[:pos], style='bold')
        match = Text(title[pos : pos + len(prefix)], style='bold green')
        after = Text(title[pos + len(prefix) :], style='bold')
        return Text.assemble(before, match, after)
