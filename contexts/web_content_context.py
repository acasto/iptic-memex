from base_classes import InteractionContext


class WebContentContext(InteractionContext):
    """
    Class for handling fetched web content
    """

    def __init__(self, session, content=None):
        self.session = session
        self.web_content = {'name': 'Fetched Web Content', 'content': content}

    def get(self):
        return self.web_content
