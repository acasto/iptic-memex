"""Inline command hint widget for the TUI input."""

from __future__ import annotations

from typing import Iterable, List, Optional

from rich.text import Text

from textual.widgets import Static, Label

from tui.models import CommandItem


class CommandHint(Static):
    """Display a short list of matching commands beneath the input box."""

    DEFAULT_MAX: Optional[int] = None  # None = show all

    def __init__(self, *args, max_items: Optional[int] = DEFAULT_MAX, **kwargs) -> None:
        super().__init__(*args, markup=True, **kwargs)
        self.max_items = max_items
        self.styles.visibility = "hidden"
        try:
            # Remove from layout until there is content
            self.styles.display = "none"
        except Exception:
            pass

    def update_suggestions(self, commands: Iterable[CommandItem], *, prefix: Optional[str] = None) -> None:
        """Render the provided command suggestions."""

        prefix = (prefix or '').strip().lower()
        items: List[CommandItem] = list(commands)
        if isinstance(self.max_items, int) and self.max_items > 0:
            items = items[: self.max_items]
        if not items:
            self.styles.visibility = "hidden"
            try:
                self.styles.display = "none"
            except Exception:
                pass
            self.update('')
            return

        # Clear existing children and mount one label per suggestion (2-column grid via CSS).
        try:
            self.remove_children()
        except Exception:
            self.update("")

        for item in items:
            if prefix:
                name = self._highlight_prefix(item.title, prefix)
            else:
                name = Text(item.title, style='bold')
            help_text = (item.help or '').strip()
            if help_text:
                line = Text.assemble(name, Text(f" — {help_text}", style='dim'))
            else:
                line = name
            self.mount(Static(line, classes="hint-item"))

        self.styles.visibility = "visible"
        try:
            self.styles.display = "block"
        except Exception:
            pass

    def show_message(self, message: str) -> None:
        """Display an informational message in place of command suggestions."""

        text = (message or '').strip()
        if not text:
            self.styles.visibility = "hidden"
            try:
                self.styles.display = "none"
            except Exception:
                pass
            self.update('')
            return
        try:
            self.remove_children()
        except Exception:
            self.update("")
        self.mount(Static('Info', classes='hint-title'))
        self.mount(Static(Text(text, style='bold'), classes='hint-item'))
        self.styles.visibility = "visible"
        try:
            self.styles.display = "block"
        except Exception:
            pass

    def show_strings(self, values: Iterable[str], *, title: str = 'Suggestions') -> None:
        """Render a simple list of string suggestions."""

        entries = [str(v) for v in values if str(v).strip()]
        if not entries:
            self.styles.visibility = "hidden"
            try:
                self.styles.display = "none"
            except Exception:
                pass
            self.update('')
            return
        try:
            self.remove_children()
        except Exception:
            self.update("")
        self.mount(Static(title, classes='hint-title'))
        for entry in entries:
            self.mount(Static(Text(entry, style='bold'), classes='hint-item'))
        self.styles.visibility = "visible"
        try:
            self.styles.display = "block"
        except Exception:
            pass

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
