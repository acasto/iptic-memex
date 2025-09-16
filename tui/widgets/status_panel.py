"""Widget responsible for logging status messages."""

from __future__ import annotations

from rich.text import Text

from textual.widgets import RichLog


class StatusPanel(RichLog):
    """Simple wrapper to standardize status logging."""

    def log_status(self, message: str, level: str = "info") -> None:
        """Log a message using colour conventions for the level provided."""

        style_map = {
            "info": "white",
            "debug": "grey50",
            "warning": "yellow",
            "error": "red",
            "critical": "bold red",
        }
        style = style_map.get(level, "white")
        self.write(Text(message, style=style))

