"""
TextualMode - TUI implementation of InteractionMode for iptic-memex.

This mode integrates with the Session architecture to provide a terminal
user interface using the Textual library.
"""

from session_handler import InteractionMode


class TextualMode(InteractionMode):
    """
    TUI mode using Textual for iptic-memex.
    
    This mode creates a Textual app and passes the session to it,
    allowing for rich terminal interface while reusing all existing
    business logic.
    """
    
    def __init__(self, session, builder=None):
        """
        Initialize the TextualMode.
        
        Args:
            session: The Session object with all business logic
            builder: SessionBuilder for creating new sessions (e.g., model switching)
        """
        self.session = session
        self.builder = builder
        
        # Initialize chat context if not already present
        if 'chat' not in self.session.context:
            self.session.add_context('chat')
    
    def start(self):
        """
        Start the TUI mode by launching the Textual app.
        """
        try:
            from .app import MemexTUIApp
            app = MemexTUIApp(self.session, self.builder)
            app.run()
        except ImportError as e:
            if 'textual' in str(e):
                print("Error: Textual library not installed.")
                print("Install with: pip install textual")
                return
            else:
                raise
        except Exception as e:
            print(f"Error starting TUI mode: {e}")
            raise