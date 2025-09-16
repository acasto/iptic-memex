"""Widget displaying attached contexts."""

from __future__ import annotations

from typing import List

from rich.panel import Panel
from rich.text import Text

from textual.reactive import reactive
from textual.widgets import Static


class ContextSummary(Static):
    """Display currently attached contexts."""

    contexts: List[str] = reactive([], layout=True)

    def update_contexts(self, contexts: List[str]) -> None:
        """Replace the visible list of contexts."""

        self.contexts = list(contexts)

    def render(self) -> Panel:
        if not self.contexts:
            body = Text("No additional contexts", style="dim")
        else:
            body = Text("\n".join(self.contexts))
        return Panel(body, title="Contexts", border_style="cyan")

