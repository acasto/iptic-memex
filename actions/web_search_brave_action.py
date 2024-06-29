from session_handler import InteractionAction


class WebSearchBraveAction(InteractionAction):
    """
    Class for counting tokens in a message
    """

    def __init__(self, session):
        self.session = session

    def run(self, message=None):
        pass