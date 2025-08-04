"""
TUI Mode - Terminal User Interface mode for iptic-memex.

This mode delegates to the Textual-based implementation in the tui package.
"""

from base_classes import InteractionMode


class TUIMode(InteractionMode):
    """
    TUI mode that delegates to the Textual implementation.
    
    This maintains consistency with other modes while keeping
    the complex TUI implementation isolated in the tui package.
    """
    
    def __init__(self, session, builder=None):
        """
        Initialize the TUI mode.
        
        Args:
            session: The Session object with all business logic
            builder: SessionBuilder for creating new sessions
        """
        self.session = session
        self.builder = builder
    
    def start(self):
        """
        Start the TUI mode by delegating to the Textual implementation.
        """
        try:
            from tui.mode import TextualMode
            textual_mode = TextualMode(self.session, self.builder)
            textual_mode.start()
        except ImportError as e:
            if 'textual' in str(e).lower():
                print("Error: TUI mode requires the 'textual' library.")
                print("Install with: pip install textual")
            else:
                print(f"Error importing TUI components: {e}")
        except Exception as e:
            print(f"Error starting TUI mode: {e}")
            raise