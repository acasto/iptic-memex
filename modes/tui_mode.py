"""
TUI Mode - Terminal User Interface mode for iptic-memex.

This mode delegates to the Textual-based implementation in the tui package.
"""

from base_classes import InteractionMode

from tui.app import MemexTUIApp


class TUIMode(InteractionMode):
    """TUI mode that launches the Textual application."""

    def __init__(self, session, builder=None):
        """Store the session and ensure chat context exists."""

        self.session = session
        self.builder = builder
        if "chat" not in self.session.context:
            self.session.add_context("chat")

    def start(self) -> None:
        """Start the Textual application."""

        try:
            app = MemexTUIApp(self.session, self.builder)
            app.run()
        except Exception as exc:
            print(f"Error starting TUI mode: {exc}")
            raise
