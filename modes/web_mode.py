"""
Web Mode - Local web interface for iptic-memex.

This mode mirrors the TUI shim pattern: keep the CLI wiring here and
delegate the actual server/app implementation to the web package.

MVP behavior: if the web app/package is not present, print guidance
without impacting other modes.
"""

from base_classes import InteractionMode


class WebMode(InteractionMode):
    """
    Web mode that delegates to an implementation under the `web` package.

    The concrete ASGI app and server runner will live in web/app.py.
    """

    def __init__(self, session, builder=None, host=None, port=None):
        """
        Initialize the Web mode.

        Args:
            session: The Session with all business logic
            builder: SessionBuilder for creating new sessions (e.g., new tabs)
            host: Optional host override
            port: Optional port override
        """
        self.session = session
        self.builder = builder
        self.host = host
        self.port = port

    def start(self):
        """
        Start the Web mode by delegating to the web implementation.
        """
        try:
            # Expect a module providing a WebApp class with start(host, port)
            from web.app import WebApp  # type: ignore
        except ImportError as e:
            print("Web mode is not yet set up.")
            print("Create web/app.py with a WebApp(session, builder).start(host, port) implementation.")
            print("For example, you could use Starlette/FastAPI + Uvicorn, or Flask + SSE.")
            print(f"Details: {e}")
            return

        try:
            app = WebApp(self.session, self.builder)
            app.start(self.host, self.port)
        except Exception as e:
            print(f"Error starting Web app: {e}")
            raise

